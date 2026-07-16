from sqlalchemy import Connection, delete, func, insert, select

from spending.models import accounts, imports, transactions


def add_account(
    conn: Connection, *, name: str, institution: str, account_type: str
) -> int:
    result = conn.execute(
        insert(accounts).values(
            name=name, institution=institution, account_type=account_type
        )
    )
    conn.commit()
    return result.inserted_primary_key[0]


def list_accounts(conn: Connection) -> list[dict]:
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
        .outerjoin(latest_txn, accounts.c.id == latest_txn.c.account_id)
        .outerjoin(latest_import, accounts.c.id == latest_import.c.account_id)
        .order_by(accounts.c.name)
    )
    rows = conn.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]


def get_account_by_name(conn: Connection, name: str) -> dict | None:
    row = conn.execute(select(accounts).where(accounts.c.name == name)).fetchone()
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
    conn.execute(delete(accounts).where(accounts.c.id == account_id))
    conn.commit()
