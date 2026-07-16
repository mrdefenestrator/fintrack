"""Asset/debt repository: CRUD + move for asset_entries within a snapshot."""

from typing import Any

from sqlalchemy import Connection, delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from finances.models import asset_entries
from finances.types import AssetEntry


def _row_to_asset_entry(row) -> AssetEntry:
    r = dict(row)
    entry: AssetEntry = {
        "kind": r["kind"],
        "name": r["name"],
    }
    # Asset-only: id for cross-reference
    if r["kind"] == "asset":
        entry["id"] = r["id"]
    optional_map = {
        "institution": "institution",
        "value": "value",
        "source": "source",
        "quantity": "quantity",
        "balance": "balance",
        "asset_ref": "assetRef",
        "interest_rate": "interestRate",
        "next_due_date": "nextDueDate",
        "as_of_date": "asOfDate",
    }
    for col, field in optional_map.items():
        val = r.get(col)
        if val is not None:
            # Money columns stay Decimal (Numeric); asset_ref stays int. No
            # float coercion — see calculations._money.
            entry[field] = val
    # Store DB id for index operations (not exposed in TypedDict)
    entry["_db_id"] = r["id"]
    return entry


def get_asset_entries(conn: Connection, snapshot_id: int) -> list[AssetEntry]:
    rows = (
        conn.execute(
            select(asset_entries)
            .where(asset_entries.c.snapshot_id == snapshot_id)
            .order_by(asset_entries.c.sort_order)
        )
        .mappings()
        .all()
    )
    return [_row_to_asset_entry(r) for r in rows]


def _next_sort_order(conn: Connection, snapshot_id: int) -> int:
    row = conn.execute(
        select(func.max(asset_entries.c.sort_order)).where(
            asset_entries.c.snapshot_id == snapshot_id
        )
    ).scalar()
    return (row or 0) + 1


def add_asset_entry(
    conn: Connection, snapshot_id: int, entry: dict[str, Any]
) -> int | None:
    sort_order = _next_sort_order(conn, snapshot_id)
    row = _entry_dict_to_row(entry, snapshot_id, sort_order)
    result = conn.execute(insert(asset_entries).values(**row))
    conn.commit()
    if entry.get("kind") == "asset":
        return result.inserted_primary_key[0]
    return None


def update_asset_entry(
    conn: Connection,
    snapshot_id: int,
    index: int,
    updates: dict[str, Any],
    delete_keys: list[str] | None = None,
) -> None:
    db_id = _index_to_db_id(conn, snapshot_id, index)
    row = (
        conn.execute(select(asset_entries).where(asset_entries.c.id == db_id))
        .mappings()
        .first()
    )
    if row is None:
        raise ValueError(f"Assets index {index} out of range")
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
        update(asset_entries)
        .where(asset_entries.c.id == db_id)
        .values(**{k: merged[k] for k in merged if k not in ("id", "snapshot_id")})
    )
    conn.commit()


def delete_asset_entry(conn: Connection, snapshot_id: int, index: int) -> None:
    rows = conn.execute(
        select(asset_entries.c.id, asset_entries.c.kind)
        .where(asset_entries.c.snapshot_id == snapshot_id)
        .order_by(asset_entries.c.sort_order)
    ).all()
    if index < 0 or index >= len(rows):
        raise ValueError(f"Assets index {index} out of range (0..{len(rows) - 1})")
    db_id, kind = rows[index]
    try:
        conn.execute(delete(asset_entries).where(asset_entries.c.id == db_id))
    except IntegrityError as e:
        # NO ACTION foreign key: this asset is still referenced by a debt
        # (asset_ref).
        conn.rollback()
        raise ValueError(
            f"Asset id {db_id} is referenced by a debt; remove or change assetRef first"
        ) from e
    conn.commit()


def move_asset_entry(
    conn: Connection, snapshot_id: int, index: int, direction: str
) -> None:
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")
    rows = conn.execute(
        select(asset_entries.c.id, asset_entries.c.sort_order)
        .where(asset_entries.c.snapshot_id == snapshot_id)
        .order_by(asset_entries.c.sort_order)
    ).all()
    n = len(rows)
    if index < 0 or index >= n:
        raise ValueError(f"Assets index {index} out of range")
    if direction == "up" and index <= 0:
        return
    if direction == "down" and index >= n - 1:
        return
    swap_idx = index - 1 if direction == "up" else index + 1
    db_id_a, order_a = rows[index]
    db_id_b, order_b = rows[swap_idx]
    conn.execute(
        update(asset_entries)
        .where(asset_entries.c.id == db_id_a)
        .values(sort_order=order_b)
    )
    conn.execute(
        update(asset_entries)
        .where(asset_entries.c.id == db_id_b)
        .values(sort_order=order_a)
    )
    conn.commit()


def _index_to_db_id(conn: Connection, snapshot_id: int, index: int) -> int:
    rows = conn.execute(
        select(asset_entries.c.id)
        .where(asset_entries.c.snapshot_id == snapshot_id)
        .order_by(asset_entries.c.sort_order)
    ).all()
    if index < 0 or index >= len(rows):
        raise ValueError(f"Assets index {index} out of range (0..{len(rows) - 1})")
    return rows[index][0]


_FIELD_TO_COL = {
    "kind": "kind",
    "name": "name",
    "institution": "institution",
    "value": "value",
    "source": "source",
    "quantity": "quantity",
    "balance": "balance",
    "assetRef": "asset_ref",
    "interestRate": "interest_rate",
    "nextDueDate": "next_due_date",
    "asOfDate": "as_of_date",
}


def _field_to_col(field: str) -> str | None:
    return _FIELD_TO_COL.get(field)


def _entry_dict_to_row(
    entry: dict[str, Any], snapshot_id: int, sort_order: int
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "kind": entry["kind"],
        "name": entry.get("name", ""),
        "sort_order": sort_order,
    }
    optional_map = {
        "institution": "institution",
        "value": "value",
        "source": "source",
        "quantity": "quantity",
        "balance": "balance",
        "assetRef": "asset_ref",
        "interestRate": "interest_rate",
        "nextDueDate": "next_due_date",
        "asOfDate": "as_of_date",
    }
    for field, col in optional_map.items():
        val = entry.get(field)
        if val is not None:
            row[col] = val
    return row
