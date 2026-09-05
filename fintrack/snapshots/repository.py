"""Snapshot repository: list, create, rename, copy, delete."""

from sqlalchemy import Connection, delete, insert, select, update

from fintrack.core.holdings import DETAIL_TABLES
from fintrack.core.models import budget_entries, holdings, snapshots

# Snapshot used by ledger flows that predate snapshot awareness (the spending
# CLI and web UI). migrate-legacy reassigns accounts to real snapshots.
DEFAULT_SNAPSHOT_NAME = "default"


def ensure_default_snapshot(conn: Connection) -> int:
    existing = get_snapshot_id(conn, DEFAULT_SNAPSHOT_NAME)
    if existing is not None:
        return existing
    return create_snapshot(conn, DEFAULT_SNAPSHOT_NAME)


def get_snapshot_id(conn: Connection, name: str) -> int | None:
    row = conn.execute(select(snapshots.c.id).where(snapshots.c.name == name)).first()
    return row[0] if row else None


def list_snapshots(conn: Connection) -> list[str]:
    rows = conn.execute(select(snapshots.c.name).order_by(snapshots.c.name)).all()
    return [r[0] for r in rows]


def create_snapshot(conn: Connection, name: str) -> int:
    result = conn.execute(insert(snapshots).values(name=name))
    conn.commit()
    return result.inserted_primary_key[0]


def rename_snapshot(conn: Connection, snapshot_id: int, new_name: str) -> None:
    conn.execute(
        update(snapshots).where(snapshots.c.id == snapshot_id).values(name=new_name)
    )
    conn.commit()


def delete_snapshot(conn: Connection, snapshot_id: int) -> None:
    # Child rows (holdings + their detail rows, budget_entries) are removed by
    # the ON DELETE CASCADE chains on their snapshot_id foreign keys.
    conn.execute(delete(snapshots).where(snapshots.c.id == snapshot_id))
    conn.commit()


# Detail-table columns that reference another holding and need the old→new id
# map applied during a copy.
_REF_COLS = ("payment_account_ref", "secured_asset_ref")

# Detail copy order: reference targets (cash, asset) before referrers
# (credit_card, loan), so the composite FKs are satisfied as rows land.
_DETAIL_COPY_ORDER = ("cash", "asset", "credit_card", "loan")


def copy_snapshot(conn: Connection, from_id: int, to_name: str) -> int:
    new_id = conn.execute(insert(snapshots).values(name=to_name)).inserted_primary_key[
        0
    ]

    # Copy every holding spine, building the one old→new id map that every
    # cross-reference (payment/secured refs, budget auto_account_ref) uses.
    # The composite FKs on the copies verify the remapping stays in-snapshot.
    old_spines = (
        conn.execute(
            select(holdings)
            .where(holdings.c.snapshot_id == from_id)
            .order_by(holdings.c.group_key, holdings.c.sort_order)
        )
        .mappings()
        .all()
    )
    id_map: dict[int, int] = {}
    for row in old_spines:
        data = {k: v for k, v in row.items() if k not in ("id", "snapshot_id")}
        data["snapshot_id"] = new_id
        id_map[row["id"]] = conn.execute(
            insert(holdings).values(**data)
        ).inserted_primary_key[0]

    for group in _DETAIL_COPY_ORDER:
        detail = DETAIL_TABLES[group]
        for row in (
            conn.execute(select(detail).where(detail.c.snapshot_id == from_id))
            .mappings()
            .all()
        ):
            data = dict(row)
            data["holding_id"] = id_map[data["holding_id"]]
            data["snapshot_id"] = new_id
            for col in _REF_COLS:
                if data.get(col) is not None:
                    data[col] = id_map.get(data[col])
            conn.execute(insert(detail).values(**data))

    # Copy budget entries, remapping auto_account_ref
    for row in (
        conn.execute(
            select(budget_entries)
            .where(budget_entries.c.snapshot_id == from_id)
            .order_by(budget_entries.c.sort_order)
        )
        .mappings()
        .all()
    ):
        data = {k: v for k, v in row.items() if k not in ("id", "snapshot_id")}
        data["snapshot_id"] = new_id
        if data.get("auto_account_ref") in id_map:
            data["auto_account_ref"] = id_map[data["auto_account_ref"]]
        conn.execute(insert(budget_entries).values(**data))

    conn.commit()
    return new_id
