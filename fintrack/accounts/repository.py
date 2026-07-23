"""Account repository: CRUD + move for accounts within a snapshot.

Dict field names ("type", "limit", "asOfDate", ...) are the external API;
they map to unified column names (account_type, credit_limit, as_of_date)
here at the repository boundary.

accounts.balance is the canonical signed balance for every account type
(negative = owed on credit cards). Saving available or limit on a credit
card recomputes balance = available - credit_limit.
"""

from typing import Any

from sqlalchemy import Connection, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from fintrack.core.coerce import to_date
from fintrack.core.models import accounts
from fintrack.core.ordering import reorder_by_positions
from fintrack.core.types import Account


def _row_to_account(row) -> Account:
    r = dict(row)
    acc: Account = {
        "id": r["id"],
        "name": r["name"],
        "type": r["account_type"],
    }
    for src, dst in (
        ("balance", "balance"),
        ("credit_limit", "limit"),
        ("available", "available"),
        ("rewards_balance", "rewards_balance"),
        ("statement_balance", "statement_balance"),
        ("statement_due_day_of_month", "statement_due_day_of_month"),
        ("payment_account_ref", "paymentAccountRef"),
        ("as_of_date", "asOfDate"),
        ("minimum_balance", "minimum_balance"),
        ("institution", "institution"),
    ):
        val = r.get(src)
        if val is not None:
            # Money columns stay Decimal (Numeric); int/str columns come back
            # as int/str from SQLite. No float coercion — see calculations._money.
            if src == "as_of_date":
                val = val.isoformat()
            acc[dst] = val
    return acc


def get_accounts(conn: Connection, snapshot_id: int) -> list[Account]:
    rows = (
        conn.execute(
            select(accounts)
            .where(accounts.c.snapshot_id == snapshot_id)
            .order_by(accounts.c.sort_order)
        )
        .mappings()
        .all()
    )
    return [_row_to_account(r) for r in rows]


def _next_sort_order(conn: Connection, snapshot_id: int) -> int:
    from sqlalchemy import func

    row = conn.execute(
        select(func.max(accounts.c.sort_order)).where(
            accounts.c.snapshot_id == snapshot_id
        )
    ).scalar()
    return (row or 0) + 1


def _derive_cc_balance(row: dict[str, Any]) -> None:
    """Set the canonical signed balance for a credit card from available/limit.

    balance = available - credit_limit (negative = amount owed), matching the
    signed ledger balance a statement import reports. Used when `available` is
    the field being edited (the Accounts sheet's model).
    """
    if row.get("account_type") != "credit_card":
        return
    available = row.get("available")
    credit_limit = row.get("credit_limit")
    if available is not None and credit_limit is not None:
        row["balance"] = available - credit_limit


def _derive_cc_available(row: dict[str, Any]) -> None:
    """Recompute a credit card's available credit = credit_limit + balance.

    balance is the canonical input (kept current by statement imports and edits),
    so editing the limit or the balance recomputes the remaining credit rather
    than overwriting what is owed (QA #10). Rows keep the invariant
    available == credit_limit + balance either way.
    """
    if row.get("account_type") != "credit_card":
        return
    balance = row.get("balance")
    credit_limit = row.get("credit_limit")
    if balance is not None and credit_limit is not None:
        row["available"] = credit_limit + balance


def add_account(conn: Connection, snapshot_id: int, account: dict[str, Any]) -> int:
    from fintrack.accounts.balance_history import record_balance

    sort_order = _next_sort_order(conn, snapshot_id)
    row = _account_dict_to_row(account, snapshot_id, sort_order)
    if row.get("balance") is None:
        _derive_cc_balance(row)
    result = conn.execute(insert(accounts).values(**row))
    conn.commit()
    account_id = result.inserted_primary_key[0]
    if row.get("balance") is not None:
        record_balance(
            conn,
            account_id=account_id,
            balance=row["balance"],
            as_of=row.get("as_of_date"),
            source="manual",
        )
    return account_id


def update_account(
    conn: Connection, snapshot_id: int, account_id: int, updates: dict[str, Any]
) -> None:
    row = (
        conn.execute(
            select(accounts).where(
                accounts.c.id == account_id,
                accounts.c.snapshot_id == snapshot_id,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ValueError(f"Account id {account_id} not found")
    merged = dict(row)
    for k, v in updates.items():
        col = _field_to_col(k)
        if col:
            merged[col] = to_date(v) if col == "as_of_date" else v
    # Keep the credit-card invariant available == credit_limit + balance. An
    # `available` edit is the input (Accounts sheet) -> derive balance; a limit
    # or balance edit keeps balance the input (QA #10) -> recompute available.
    if "available" in updates.keys():
        _derive_cc_balance(merged)
    elif {"limit", "balance"} & updates.keys():
        _derive_cc_available(merged)
    conn.execute(
        update(accounts)
        .where(accounts.c.id == account_id, accounts.c.snapshot_id == snapshot_id)
        .values(**{k: merged[k] for k in merged if k not in ("id", "snapshot_id")})
    )
    conn.commit()
    # A balance change (direct edit, or derived from a CC available/limit
    # edit) is a new manual point in the account's balance history.
    if (
        {"balance", "available", "limit"} & updates.keys()
        and merged.get("balance") is not None
        and merged["balance"] != row["balance"]
    ):
        from fintrack.accounts.balance_history import record_balance

        record_balance(
            conn,
            account_id=account_id,
            balance=merged["balance"],
            source="manual",
        )


def delete_account(conn: Connection, snapshot_id: int, account_id: int) -> None:
    try:
        result = conn.execute(
            delete(accounts).where(
                accounts.c.id == account_id,
                accounts.c.snapshot_id == snapshot_id,
            )
        )
    except IntegrityError as e:
        # NO ACTION foreign key: the account is still referenced by a budget
        # entry (auto_account_ref) or a credit card's paymentAccountRef.
        conn.rollback()
        raise ValueError(
            f"Account id {account_id} is referenced by a budget entry or a "
            "credit card's paymentAccountRef; remove or change the reference first"
        ) from e
    if result.rowcount == 0:
        raise ValueError(f"Account id {account_id} not found")
    conn.commit()


def move_account(
    conn: Connection, snapshot_id: int, account_id: int, direction: str
) -> None:
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")
    rows = conn.execute(
        select(accounts.c.id, accounts.c.sort_order)
        .where(accounts.c.snapshot_id == snapshot_id)
        .order_by(accounts.c.sort_order)
    ).all()
    ids = [r[0] for r in rows]
    if account_id not in ids:
        raise ValueError(f"Account id {account_id} not found")
    idx = ids.index(account_id)
    if direction == "up" and idx <= 0:
        return
    if direction == "down" and idx >= len(ids) - 1:
        return
    swap_idx = idx - 1 if direction == "up" else idx + 1
    swap_id = ids[swap_idx]
    order_a = rows[idx][1]
    order_b = rows[swap_idx][1]
    conn.execute(
        update(accounts).where(accounts.c.id == account_id).values(sort_order=order_b)
    )
    conn.execute(
        update(accounts).where(accounts.c.id == swap_id).values(sort_order=order_a)
    )
    conn.commit()


def reorder_accounts(conn: Connection, snapshot_id: int, new_order: list[int]) -> None:
    """Persist a drag-reordered account order (see core.ordering)."""
    reorder_by_positions(conn, accounts, snapshot_id, new_order)


_COL_TO_FIELD = {
    "payment_account_ref": "paymentAccountRef",
    "as_of_date": "asOfDate",
    "statement_due_day_of_month": "statement_due_day_of_month",
}
_FIELD_TO_COL = {v: k for k, v in _COL_TO_FIELD.items()}
_FIELD_TO_COL.update(
    {
        "balance": "balance",
        "limit": "credit_limit",
        "available": "available",
        "rewards_balance": "rewards_balance",
        "statement_balance": "statement_balance",
        "minimum_balance": "minimum_balance",
        "institution": "institution",
        "name": "name",
        "type": "account_type",
    }
)


def _field_to_col(field: str) -> str | None:
    return _FIELD_TO_COL.get(field)


def _account_dict_to_row(
    account: dict[str, Any], snapshot_id: int, sort_order: int
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "name": account.get("name", ""),
        "account_type": account.get("type", "checking"),
        "sort_order": sort_order,
    }
    optional_map = {
        "balance": "balance",
        "limit": "credit_limit",
        "available": "available",
        "rewards_balance": "rewards_balance",
        "statement_balance": "statement_balance",
        "statement_due_day_of_month": "statement_due_day_of_month",
        "paymentAccountRef": "payment_account_ref",
        "asOfDate": "as_of_date",
        "minimum_balance": "minimum_balance",
        "institution": "institution",
    }
    for field, col in optional_map.items():
        val = account.get(field)
        if val is not None:
            row[col] = to_date(val) if col == "as_of_date" else val
    return row
