"""Budget repository: CRUD + move for budget entries within a snapshot."""

from typing import Any

from sqlalchemy import Connection, delete, func, insert, select, update

from finances.models import budget_entries
from finances.types import BudgetEntry


def _row_to_budget_entry(row) -> BudgetEntry:
    r = dict(row)
    entry: BudgetEntry = {
        "kind": r["kind"],
        "description": r["description"],
        "amount": r["amount"],  # Decimal (Numeric); see calculations._money
        "recurrence": r["recurrence"],
    }
    optional_map = {
        "type": "type",
        "date": "date",
        "day_of_month": "dayOfMonth",
        "month": "month",
        "day_of_year": "dayOfYear",
        "continuous": "continuous",
        "auto_account_ref": "autoAccountRef",
    }
    for col, field in optional_map.items():
        val = r.get(col)
        if val is not None:
            entry[field] = val
    # Store DB row id as _db_id for index operations
    entry["_db_id"] = r["id"]
    return entry


def get_budget_entries(conn: Connection, snapshot_id: int) -> list[BudgetEntry]:
    rows = (
        conn.execute(
            select(budget_entries)
            .where(budget_entries.c.snapshot_id == snapshot_id)
            .order_by(budget_entries.c.sort_order)
        )
        .mappings()
        .all()
    )
    return [_row_to_budget_entry(r) for r in rows]


def _next_sort_order(conn: Connection, snapshot_id: int) -> int:
    row = conn.execute(
        select(func.max(budget_entries.c.sort_order)).where(
            budget_entries.c.snapshot_id == snapshot_id
        )
    ).scalar()
    return (row or 0) + 1


def add_budget_entry(conn: Connection, snapshot_id: int, entry: dict[str, Any]) -> None:
    sort_order = _next_sort_order(conn, snapshot_id)
    row = _entry_dict_to_row(entry, snapshot_id, sort_order)
    conn.execute(insert(budget_entries).values(**row))
    conn.commit()


def update_budget_entry(
    conn: Connection,
    snapshot_id: int,
    index: int,
    updates: dict[str, Any],
    delete_keys: list[str] | None = None,
) -> None:
    db_id = _index_to_db_id(conn, snapshot_id, index)
    row = (
        conn.execute(select(budget_entries).where(budget_entries.c.id == db_id))
        .mappings()
        .first()
    )
    if row is None:
        raise ValueError(f"Budget index {index} out of range")
    merged = dict(row)
    for k, v in updates.items():
        col = _field_to_col(k)
        if col:
            merged[col] = v
    for k in delete_keys or []:
        col = _field_to_col(k)
        if col:
            merged[col] = None
    conn.execute(
        update(budget_entries)
        .where(budget_entries.c.id == db_id)
        .values(**{k: merged[k] for k in merged if k not in ("id", "snapshot_id")})
    )
    conn.commit()


def delete_budget_entry(conn: Connection, snapshot_id: int, index: int) -> None:
    db_id = _index_to_db_id(conn, snapshot_id, index)
    conn.execute(delete(budget_entries).where(budget_entries.c.id == db_id))
    conn.commit()


def move_budget_entry(
    conn: Connection, snapshot_id: int, index: int, direction: str
) -> None:
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")
    rows = conn.execute(
        select(budget_entries.c.id, budget_entries.c.sort_order)
        .where(budget_entries.c.snapshot_id == snapshot_id)
        .order_by(budget_entries.c.sort_order)
    ).all()
    n = len(rows)
    if index < 0 or index >= n:
        raise ValueError(f"Budget index {index} out of range (0..{n - 1})")
    if direction == "up" and index <= 0:
        return
    if direction == "down" and index >= n - 1:
        return
    swap_idx = index - 1 if direction == "up" else index + 1
    db_id_a, order_a = rows[index]
    db_id_b, order_b = rows[swap_idx]
    conn.execute(
        update(budget_entries)
        .where(budget_entries.c.id == db_id_a)
        .values(sort_order=order_b)
    )
    conn.execute(
        update(budget_entries)
        .where(budget_entries.c.id == db_id_b)
        .values(sort_order=order_a)
    )
    conn.commit()


def _index_to_db_id(conn: Connection, snapshot_id: int, index: int) -> int:
    rows = conn.execute(
        select(budget_entries.c.id)
        .where(budget_entries.c.snapshot_id == snapshot_id)
        .order_by(budget_entries.c.sort_order)
    ).all()
    if index < 0 or index >= len(rows):
        raise ValueError(f"Budget index {index} out of range (0..{len(rows) - 1})")
    return rows[index][0]


_FIELD_TO_COL = {
    "kind": "kind",
    "description": "description",
    "amount": "amount",
    "recurrence": "recurrence",
    "type": "type",
    "date": "date",
    "dayOfMonth": "day_of_month",
    "month": "month",
    "dayOfYear": "day_of_year",
    "continuous": "continuous",
    "autoAccountRef": "auto_account_ref",
}


def _field_to_col(field: str) -> str | None:
    return _FIELD_TO_COL.get(field)


def _entry_dict_to_row(
    entry: dict[str, Any], snapshot_id: int, sort_order: int
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "kind": entry["kind"],
        "description": entry.get("description", ""),
        "amount": entry.get("amount", 0),
        "recurrence": entry.get("recurrence", "monthly"),
        "sort_order": sort_order,
    }
    optional_map = {
        "type": "type",
        "date": "date",
        "dayOfMonth": "day_of_month",
        "month": "month",
        "dayOfYear": "day_of_year",
        "continuous": "continuous",
        "autoAccountRef": "auto_account_ref",
    }
    for field, col in optional_map.items():
        val = entry.get(field)
        if val is not None:
            row[col] = val
    return row
