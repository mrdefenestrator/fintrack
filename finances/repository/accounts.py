"""Account repository: CRUD + move for accounts within a snapshot."""

from typing import Any

from sqlalchemy import Connection, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from finances.models import accounts
from finances.types import Account


def _row_to_account(row) -> Account:
    r = dict(row)
    acc: Account = {
        "id": r["id"],
        "name": r["name"],
        "type": r["type"],
    }
    for src, dst in (
        ("balance", "balance"),
        ("limit", "limit"),
        ("available", "available"),
        ("rewards_balance", "rewards_balance"),
        ("statement_balance", "statement_balance"),
        ("statement_due_day_of_month", "statement_due_day_of_month"),
        ("payment_account_ref", "paymentAccountRef"),
        ("as_of_date", "asOfDate"),
        ("minimum_balance", "minimum_balance"),
        ("institution", "institution"),
        ("partial_account_number", "partial_account_number"),
    ):
        val = r.get(src)
        if val is not None:
            # Money columns stay Decimal (Numeric); int/str columns come back
            # as int/str from SQLite. No float coercion — see calculations._money.
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


def add_account(conn: Connection, snapshot_id: int, account: dict[str, Any]) -> int:
    sort_order = _next_sort_order(conn, snapshot_id)
    row = _account_dict_to_row(account, snapshot_id, sort_order)
    result = conn.execute(insert(accounts).values(**row))
    conn.commit()
    return result.inserted_primary_key[0]


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
            merged[col] = v
    conn.execute(
        update(accounts)
        .where(accounts.c.id == account_id, accounts.c.snapshot_id == snapshot_id)
        .values(**{k: merged[k] for k in merged if k not in ("id", "snapshot_id")})
    )
    conn.commit()


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


_COL_TO_FIELD = {
    "payment_account_ref": "paymentAccountRef",
    "as_of_date": "asOfDate",
    "statement_due_day_of_month": "statement_due_day_of_month",
}
_FIELD_TO_COL = {v: k for k, v in _COL_TO_FIELD.items()}
_FIELD_TO_COL.update(
    {
        "balance": "balance",
        "limit": "limit",
        "available": "available",
        "rewards_balance": "rewards_balance",
        "statement_balance": "statement_balance",
        "minimum_balance": "minimum_balance",
        "institution": "institution",
        "partial_account_number": "partial_account_number",
        "name": "name",
        "type": "type",
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
        "type": account.get("type", "checking"),
        "sort_order": sort_order,
    }
    optional_map = {
        "balance": "balance",
        "limit": "limit",
        "available": "available",
        "rewards_balance": "rewards_balance",
        "statement_balance": "statement_balance",
        "statement_due_day_of_month": "statement_due_day_of_month",
        "paymentAccountRef": "payment_account_ref",
        "asOfDate": "as_of_date",
        "minimum_balance": "minimum_balance",
        "institution": "institution",
        "partial_account_number": "partial_account_number",
    }
    for field, col in optional_map.items():
        val = account.get(field)
        if val is not None:
            row[col] = val
    return row
