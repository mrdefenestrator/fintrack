"""Account repository: CRUD + move for cash and credit-card holdings.

Dict field names ("type", "limit", "asOfDate", ...) are the external API;
they map to the holdings spine + cash_details/credit_card_details columns
here at the repository boundary.

`balance` is the canonical signed balance for every account-style holding
(negative = owed on credit cards). A credit card's `available` is no longer
stored: it is computed as credit_limit + balance at read time, so it can
never drift from the imported balance. Loans are holdings of the loan group
and are served by fintrack.networth.repository; add_account/update_account
still accept type="loan" transitionally (routing the row to the loan group),
but get_accounts returns cash + credit cards only.
"""

from datetime import date
from typing import Any

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from fintrack.core.coerce import to_date
from fintrack.core.holdings import (
    delete_holding,
    get_holding,
    insert_holding,
    load_holdings,
    reorder_merged,
    swap_adjacent,
    update_holding,
)
from fintrack.core.types import Account, group_for_account_type

_ACCOUNT_GROUPS = ("cash", "credit_card")
# Groups an account-style id may address for edit/delete (loan kept
# transitionally for the CLI until the loan commands take over).
_EDITABLE_GROUPS = ("cash", "credit_card", "loan")

_SPINE_FIELD_TO_COL = {
    "name": "name",
    "institution": "institution",
    "type": "type",
    "asOfDate": "as_of_date",
}

# Per-group detail columns, as dict-field -> column maps.
_DETAIL_FIELD_TO_COL: dict[str, dict[str, str]] = {
    "cash": {
        "balance": "balance",
        "minimum_balance": "minimum_balance",
    },
    "credit_card": {
        "balance": "balance",
        "limit": "credit_limit",
        "rewards_balance": "rewards_balance",
        "statement_balance": "statement_balance",
        "statement_due_day_of_month": "statement_due_day_of_month",
        "paymentAccountRef": "payment_account_ref",
    },
    "loan": {
        "balance": "balance",
        "statement_due_day_of_month": "statement_due_day_of_month",
        "paymentAccountRef": "payment_account_ref",
    },
}

_COL_TO_FIELD = {
    "credit_limit": "limit",
    "payment_account_ref": "paymentAccountRef",
}


def _row_to_account(row: dict[str, Any]) -> Account:
    acc: Account = {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
    }
    for col in (
        "balance",
        "credit_limit",
        "rewards_balance",
        "statement_balance",
        "statement_due_day_of_month",
        "payment_account_ref",
        "minimum_balance",
        "institution",
    ):
        val = row.get(col)
        if val is not None:
            # Money columns stay Decimal (Numeric); int/str columns come back
            # as int/str from SQLite. No float coercion — see calculations._money.
            acc[_COL_TO_FIELD.get(col, col)] = val
    if row.get("as_of_date") is not None:
        acc["asOfDate"] = row["as_of_date"].isoformat()
    # Computed, never stored: remaining credit on a card.
    if (
        row["group_key"] == "credit_card"
        and row.get("credit_limit") is not None
        and row.get("balance") is not None
    ):
        acc["available"] = row["credit_limit"] + row["balance"]
    return acc


def get_accounts(conn: Connection, snapshot_id: int) -> list[Account]:
    rows = load_holdings(conn, snapshot_id, _ACCOUNT_GROUPS)
    return [_row_to_account(r) for r in rows]


def _derive_cc_balance(merged: dict[str, Any], available: Any) -> None:
    """Set the canonical signed balance from an `available` input (the old
    Accounts-sheet model): balance = available - credit_limit."""
    credit_limit = merged.get("credit_limit")
    if available is not None and credit_limit is not None:
        merged["balance"] = available - credit_limit


def add_account(
    conn: Connection,
    snapshot_id: int,
    account: dict[str, Any],
    *,
    today: date | None = None,
) -> int:
    from fintrack.accounts.balance_history import record_balance

    account_type = account.get("type", "checking")
    group = group_for_account_type(account_type)
    spine = {"type": account_type, "name": account.get("name", "")}
    for field in ("institution", "asOfDate"):
        val = account.get(field)
        if val is not None:
            spine[_SPINE_FIELD_TO_COL[field]] = (
                to_date(val) if field == "asOfDate" else val
            )
    detail: dict[str, Any] = {}
    for field, col in _DETAIL_FIELD_TO_COL[group].items():
        val = account.get(field)
        if val is not None:
            detail[col] = val
    if group == "credit_card" and detail.get("balance") is None:
        _derive_cc_balance(detail, account.get("available"))
        if detail.get("balance") is None:
            detail.pop("balance", None)
    # IntegrityError (duplicate importable name, invalid linked account) is
    # allowed to propagate, matching the pre-split dict API.
    account_id = insert_holding(conn, snapshot_id, group, spine, detail)
    conn.commit()
    if detail.get("balance") is not None:
        record_balance(
            conn,
            account_id=account_id,
            balance=detail["balance"],
            # An explicit asOfDate wins; otherwise the caller's local date
            # (browser's, QA #6), falling back to the server's in record_balance.
            as_of=spine.get("as_of_date") or today,
            source="manual",
        )
    return account_id


def update_account(
    conn: Connection,
    snapshot_id: int,
    account_id: int,
    updates: dict[str, Any],
    *,
    today: date | None = None,
) -> None:
    """Update an account. A balance change records a new manual balance-history
    point; `today` (the caller's local date — e.g. the browser's, QA #6) dates
    that point, falling back to the server's local date when omitted. A type
    edit that changes the holding's group (e.g. savings -> credit_card) moves
    the detail row, carrying the shared fields."""
    row = get_holding(conn, snapshot_id, account_id)
    if row is None or row["group_key"] not in _EDITABLE_GROUPS:
        raise ValueError(f"Account id {account_id} not found")
    old_group = row["group_key"]
    new_type = updates.get("type", row["type"])
    new_group = group_for_account_type(new_type)
    dmap = _DETAIL_FIELD_TO_COL[new_group]

    merged = dict(row)
    spine_updates: dict[str, Any] = {}
    for field, val in updates.items():
        if field in _SPINE_FIELD_TO_COL:
            col = _SPINE_FIELD_TO_COL[field]
            spine_updates[col] = to_date(val) if col == "as_of_date" else val
            merged[col] = spine_updates[col]
        elif field in dmap:
            merged[dmap[field]] = val
    # Credit-card rule (QA #10): an `available` edit is the input -> derive
    # balance; a limit or balance edit keeps balance the input and available
    # is computed at read time, so nothing else to store.
    if new_group == "credit_card" and "available" in updates:
        _derive_cc_balance(merged, updates["available"])
    detail_values = {col: merged.get(col) for col in dmap.values()}

    # IntegrityError (duplicate name, invalid linked account, or a type change
    # blocked by import history) is allowed to propagate, matching the
    # pre-split dict API.
    update_holding(
        conn,
        snapshot_id,
        account_id,
        old_group,
        new_group,
        spine_updates,
        detail_values,
    )
    conn.commit()
    # A balance change (direct edit, or derived from a CC available edit) is a
    # new manual point in the account's balance history.
    if (
        {"balance", "available", "limit"} & updates.keys()
        and merged.get("balance") is not None
        and merged["balance"] != row.get("balance")
    ):
        from fintrack.accounts.balance_history import record_balance

        record_balance(
            conn,
            account_id=account_id,
            balance=merged["balance"],
            as_of=today,
            source="manual",
        )


def delete_account(conn: Connection, snapshot_id: int, account_id: int) -> None:
    try:
        deleted = delete_holding(conn, snapshot_id, account_id, _EDITABLE_GROUPS)
    except IntegrityError as e:
        # NO ACTION foreign key: the account is still referenced by a budget
        # entry (auto_account_ref) or a credit card's paymentAccountRef.
        conn.rollback()
        raise ValueError(
            f"Account id {account_id} is referenced by a budget entry or a "
            "credit card's paymentAccountRef; remove or change the reference first"
        ) from e
    if deleted == 0:
        raise ValueError(f"Account id {account_id} not found")
    conn.commit()


def move_account(
    conn: Connection, snapshot_id: int, account_id: int, direction: str
) -> None:
    swap_adjacent(conn, snapshot_id, _ACCOUNT_GROUPS, account_id, direction)
    conn.commit()


def reorder_accounts(conn: Connection, snapshot_id: int, new_order: list[int]) -> None:
    """Persist a drag-reordered account order (permutation of the merged
    cash + credit-card list, group-locally decomposed)."""
    reorder_merged(conn, snapshot_id, _ACCOUNT_GROUPS, new_order)
    conn.commit()
