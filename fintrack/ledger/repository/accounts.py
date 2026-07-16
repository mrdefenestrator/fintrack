"""Ledger-style account functions (from the spending app).

Transitional API kept for the spending CLI and web UI, which predate snapshot
awareness: every function operates within one snapshot, defaulting to the
shared "default" snapshot when none is given. The finances-style CRUD over
the same table lives in fintrack.accounts.repository.
"""

from sqlalchemy import Connection, delete, func, insert, select
from sqlalchemy.exc import IntegrityError

from fintrack.core.models import accounts, imports, transactions
from fintrack.snapshots.repository import ensure_default_snapshot


def _resolve_snapshot_id(conn: Connection, snapshot_id: int | None) -> int:
    return snapshot_id if snapshot_id is not None else ensure_default_snapshot(conn)


def add_account(
    conn: Connection,
    *,
    name: str,
    institution: str,
    account_type: str,
    snapshot_id: int | None = None,
) -> int:
    sid = _resolve_snapshot_id(conn, snapshot_id)
    next_order = (
        conn.execute(
            select(func.max(accounts.c.sort_order)).where(accounts.c.snapshot_id == sid)
        ).scalar()
        or 0
    ) + 1
    result = conn.execute(
        insert(accounts).values(
            snapshot_id=sid,
            name=name,
            institution=institution,
            account_type=account_type,
            sort_order=next_order,
        )
    )
    conn.commit()
    return result.inserted_primary_key[0]


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
        select(accounts, latest_txn.c.latest_txn_date, latest_import.c.latest_import_at)
        .where(accounts.c.snapshot_id == sid)
        .outerjoin(latest_txn, accounts.c.id == latest_txn.c.account_id)
        .outerjoin(latest_import, accounts.c.id == latest_import.c.account_id)
        .order_by(accounts.c.name)
    )
    rows = conn.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]


def get_account_by_name(
    conn: Connection, name: str, snapshot_id: int | None = None
) -> dict | None:
    sid = _resolve_snapshot_id(conn, snapshot_id)
    row = conn.execute(
        select(accounts).where(accounts.c.name == name, accounts.c.snapshot_id == sid)
    ).fetchone()
    return dict(row._mapping) if row else None


def get_account_by_id(conn: Connection, account_id: int) -> dict | None:
    row = conn.execute(select(accounts).where(accounts.c.id == account_id)).fetchone()
    return dict(row._mapping) if row else None


def edit_account(
    conn: Connection,
    account_id: int,
    *,
    name: str | None = None,
    institution: str | None = None,
    account_type: str | None = None,
) -> None:
    values = {}
    if name is not None:
        values["name"] = name
    if institution is not None:
        values["institution"] = institution
    if account_type is not None:
        values["account_type"] = account_type
    if values:
        conn.execute(
            accounts.update().where(accounts.c.id == account_id).values(**values)
        )
        conn.commit()


def delete_account(conn: Connection, account_id: int) -> None:
    """Delete an account; its imports and transactions cascade with it."""
    try:
        conn.execute(delete(accounts).where(accounts.c.id == account_id))
    except IntegrityError as e:
        # NO ACTION foreign key: referenced as another account's autopay
        # source (payment_account_ref) or by a budget entry (auto_account_ref).
        conn.rollback()
        raise ValueError(
            f"Account id {account_id} is referenced by a budget entry or a "
            "credit card's paymentAccountRef; remove or change the reference first"
        ) from e
    conn.commit()
