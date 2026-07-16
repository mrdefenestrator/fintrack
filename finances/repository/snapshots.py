"""Snapshot repository: list, create, rename, copy, delete."""

from sqlalchemy import Connection, delete, insert, select, update

from finances.models import accounts, asset_entries, budget_entries, snapshots


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
    # Child rows (accounts, budget_entries, asset_entries) are removed by the
    # ON DELETE CASCADE on their snapshot_id foreign key.
    conn.execute(delete(snapshots).where(snapshots.c.id == snapshot_id))
    conn.commit()


def copy_snapshot(conn: Connection, from_id: int, to_name: str) -> int:
    new_id = conn.execute(insert(snapshots).values(name=to_name)).inserted_primary_key[
        0
    ]

    # Copy accounts, building old→new id map for cross-reference rewriting
    old_accounts = (
        conn.execute(
            select(accounts)
            .where(accounts.c.snapshot_id == from_id)
            .order_by(accounts.c.sort_order)
        )
        .mappings()
        .all()
    )
    account_id_map: dict[int, int] = {}
    for row in old_accounts:
        data = {k: v for k, v in row.items() if k not in ("id", "snapshot_id")}
        data["snapshot_id"] = new_id
        new_acc_id = conn.execute(insert(accounts).values(**data)).inserted_primary_key[
            0
        ]
        account_id_map[row["id"]] = new_acc_id

    # Rewrite payment_account_ref
    for old_acc in old_accounts:
        old_ref = old_acc["payment_account_ref"]
        if old_ref is not None and old_ref in account_id_map:
            new_acc_id = account_id_map[old_acc["id"]]
            conn.execute(
                update(accounts)
                .where(accounts.c.id == new_acc_id)
                .values(payment_account_ref=account_id_map[old_ref])
            )

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
        if data.get("auto_account_ref") in account_id_map:
            data["auto_account_ref"] = account_id_map[data["auto_account_ref"]]
        conn.execute(insert(budget_entries).values(**data))

    # Copy asset entries, building old→new id map for asset_ref rewriting
    old_assets = (
        conn.execute(
            select(asset_entries)
            .where(asset_entries.c.snapshot_id == from_id)
            .order_by(asset_entries.c.sort_order)
        )
        .mappings()
        .all()
    )
    asset_id_map: dict[int, int] = {}
    for row in old_assets:
        data = {k: v for k, v in row.items() if k not in ("id", "snapshot_id")}
        data["snapshot_id"] = new_id
        new_asset_id = conn.execute(
            insert(asset_entries).values(**data)
        ).inserted_primary_key[0]
        asset_id_map[row["id"]] = new_asset_id

    # Rewrite asset_ref for debt entries
    for old_asset in old_assets:
        if old_asset.get("asset_ref") is not None:
            new_asset_id = asset_id_map[old_asset["id"]]
            old_ref = old_asset["asset_ref"]
            if old_ref in asset_id_map:
                conn.execute(
                    update(asset_entries)
                    .where(asset_entries.c.id == new_asset_id)
                    .values(asset_ref=asset_id_map[old_ref])
                )

    conn.commit()
    return new_id
