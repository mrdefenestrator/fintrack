"""Ledger-style account functions (from the spending app).

Transitional API kept for the spending CLI and web UI, which predate snapshot
awareness: every function operates within one snapshot, defaulting to the
shared "default" snapshot when none is given. Rows here are the importable
holdings (cash, credit cards, loans); dicts expose the holding's type under
the historical `account_type` key. The finances-style CRUD over the same
tables lives in fintrack.accounts.repository.
"""

from sqlalchemy import Connection, func, select
from sqlalchemy.exc import IntegrityError

from fintrack.core.holdings import (
    DETAIL_TABLES,
    delete_holding,
    insert_holding,
    update_holding,
)
from fintrack.core.models import holdings, imports, transactions
from fintrack.core.types import IMPORTABLE_GROUPS, group_for_account_type
from fintrack.snapshots.repository import ensure_default_snapshot


def _resolve_snapshot_id(conn: Connection, snapshot_id: int | None) -> int:
    return snapshot_id if snapshot_id is not None else ensure_default_snapshot(conn)


def _row_to_dict(row) -> dict:
    d = dict(row._mapping)
    d["account_type"] = d.pop("type")
    return d


def add_account(
    conn: Connection,
    *,
    name: str,
    institution: str,
    account_type: str,
    snapshot_id: int | None = None,
) -> int:
    sid = _resolve_snapshot_id(conn, snapshot_id)
    group = group_for_account_type(account_type)
    holding_id = insert_holding(
        conn,
        sid,
        group,
        {"type": account_type, "name": name, "institution": institution},
        {},
    )
    conn.commit()
    return holding_id


def list_accounts(conn: Connection, snapshot_id: int | None = None) -> list[dict]:
    sid = _resolve_snapshot_id(conn, snapshot_id)
    latest_txn = (
        select(
            transactions.c.account_id,
            func.max(transactions.c.date).label("latest_txn_date"),
        )
        .group_by(transactions.c.account_id)
        .subquery()
    )
    latest_import = (
        select(
            imports.c.account_id,
            func.max(imports.c.imported_at).label("latest_import_at"),
        )
        .where(imports.c.status == "confirmed")
        .group_by(imports.c.account_id)
        .subquery()
    )
    stmt = (
        select(holdings, latest_txn.c.latest_txn_date, latest_import.c.latest_import_at)
        .where(
            holdings.c.snapshot_id == sid,
            holdings.c.group_key.in_(IMPORTABLE_GROUPS),
        )
        .outerjoin(latest_txn, holdings.c.id == latest_txn.c.account_id)
        .outerjoin(latest_import, holdings.c.id == latest_import.c.account_id)
        # Match the Holdings page order (group band, then in-group order) so
        # the account dropdowns in Import / Transactions feel familiar; name
        # is a stable tiebreak.
        .order_by(holdings.c.group_key, holdings.c.sort_order, holdings.c.name)
    )
    rows = conn.execute(stmt).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_account_by_name(
    conn: Connection, name: str, snapshot_id: int | None = None
) -> dict | None:
    sid = _resolve_snapshot_id(conn, snapshot_id)
    row = conn.execute(
        select(holdings).where(
            holdings.c.name == name,
            holdings.c.snapshot_id == sid,
            holdings.c.group_key.in_(IMPORTABLE_GROUPS),
        )
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_account_by_id(conn: Connection, account_id: int) -> dict | None:
    """Full account row: spine + detail columns merged, with `account_type`
    (historical alias for the holding type) and a computed credit-card
    `available` (= credit_limit + balance), so the ledger dict matches the
    old flat accounts row that callers read balance/available/limit off."""
    spine = conn.execute(
        select(holdings).where(
            holdings.c.id == account_id,
            holdings.c.group_key.in_(IMPORTABLE_GROUPS),
        )
    ).fetchone()
    if spine is None:
        return None
    d = _row_to_dict(spine)
    group = d["group_key"]
    detail = DETAIL_TABLES[group]
    detail_row = conn.execute(
        select(detail).where(detail.c.holding_id == account_id)
    ).fetchone()
    if detail_row is not None:
        for k, v in detail_row._mapping.items():
            if k not in ("holding_id", "group_key", "snapshot_id"):
                d[k] = v
    # The old flat accounts row always carried credit_limit/available keys
    # (None for non-cards). available is computed, never stored (D4).
    d.setdefault("credit_limit", None)
    if group == "credit_card":
        limit, bal = d.get("credit_limit"), d.get("balance")
        d["available"] = (
            (limit + bal) if (limit is not None and bal is not None) else None
        )
    else:
        d["available"] = None
    return d


def edit_account(
    conn: Connection,
    account_id: int,
    *,
    name: str | None = None,
    institution: str | None = None,
    account_type: str | None = None,
) -> None:
    row = conn.execute(
        select(holdings.c.snapshot_id, holdings.c.group_key, holdings.c.type).where(
            holdings.c.id == account_id,
            holdings.c.group_key.in_(IMPORTABLE_GROUPS),
        )
    ).first()
    if row is None:
        return
    spine: dict = {}
    if name is not None:
        spine["name"] = name
    if institution is not None:
        spine["institution"] = institution
    if account_type is not None:
        spine["type"] = account_type
    if not spine:
        return
    new_group = group_for_account_type(spine.get("type", row.type))
    detail: dict = {}
    if new_group != row.group_key:
        # This API only edits identity columns, but a group change rebuilds
        # the detail row — carry the shared balance so retyping never drops it.
        from fintrack.core.holdings import get_holding

        old = get_holding(conn, row.snapshot_id, account_id) or {}
        detail["balance"] = old.get("balance")
    try:
        update_holding(
            conn, row.snapshot_id, account_id, row.group_key, new_group, spine, detail
        )
        conn.commit()
    except IntegrityError as e:
        conn.rollback()
        raise ValueError(
            "Edit rejected: duplicate name for this institution, or the "
            "account is referenced/pinned by other rows"
        ) from e


def delete_account(conn: Connection, account_id: int) -> None:
    """Delete an account; its imports and transactions cascade with it."""
    row = conn.execute(
        select(holdings.c.snapshot_id).where(holdings.c.id == account_id)
    ).first()
    if row is None:
        return
    try:
        delete_holding(conn, row.snapshot_id, account_id, IMPORTABLE_GROUPS)
    except IntegrityError as e:
        # NO ACTION foreign key: referenced as another account's autopay
        # source (payment_account_ref) or by a budget entry (auto_account_ref).
        conn.rollback()
        raise ValueError(
            f"Account id {account_id} is referenced by a budget entry or a "
            "credit card's paymentAccountRef; remove or change the reference first"
        ) from e
    conn.commit()
