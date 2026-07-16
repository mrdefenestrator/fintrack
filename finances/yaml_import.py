"""Import a YAML finances file into the SQLite database as a named snapshot.

Usage:
    uv run python -m finances.yaml_import data/mike.yaml [--db finances.db] [--name mike]
    uv run python -m finances.yaml_import data/  # import all YAML files in a directory
"""

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Connection, insert, select, update

from finances.db import get_engine, init_db
from finances.models import accounts, asset_entries, budget_entries, snapshots


def import_yaml(conn: Connection, path: Path, name: str | None = None) -> int:
    """Import a single YAML file as a snapshot. Returns new snapshot id.

    Raises ValueError if a snapshot with this name already exists.
    """
    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    snapshot_name = name or path.stem
    existing = conn.execute(
        select(snapshots.c.id).where(snapshots.c.name == snapshot_name)
    ).first()
    if existing:
        raise ValueError(f"Snapshot '{snapshot_name}' already exists")

    snapshot_id = conn.execute(
        insert(snapshots).values(name=snapshot_name)
    ).inserted_primary_key[0]

    # Import accounts; build yaml_id → db_id map for cross-reference rewriting
    account_id_map: dict[int, int] = {}
    for sort_order, acc in enumerate(data.get("accounts") or []):
        yaml_id = acc.get("id")
        row: dict[str, Any] = {
            "snapshot_id": snapshot_id,
            "name": acc.get("name", ""),
            "type": acc.get("type", "checking"),
            "sort_order": sort_order,
        }
        for field, col in (
            ("balance", "balance"),
            ("limit", "limit"),
            ("available", "available"),
            ("rewards_balance", "rewards_balance"),
            ("statement_balance", "statement_balance"),
            ("statement_due_day_of_month", "statement_due_day_of_month"),
            ("asOfDate", "as_of_date"),
            ("minimum_balance", "minimum_balance"),
            ("institution", "institution"),
            ("partial_account_number", "partial_account_number"),
        ):
            val = acc.get(field)
            if val is not None:
                row[col] = val
        db_id = conn.execute(insert(accounts).values(**row)).inserted_primary_key[0]
        if yaml_id is not None:
            account_id_map[yaml_id] = db_id

    # Rewrite payment_account_ref after all accounts are inserted
    for acc in data.get("accounts") or []:
        yaml_id = acc.get("id")
        ref = acc.get("paymentAccountRef")
        if yaml_id is not None and ref is not None and ref in account_id_map:
            db_id = account_id_map[yaml_id]
            conn.execute(
                update(accounts)
                .where(accounts.c.id == db_id)
                .values(payment_account_ref=account_id_map[ref])
            )

    # Import budget entries
    for sort_order, entry in enumerate(data.get("budget") or []):
        row = {
            "snapshot_id": snapshot_id,
            "kind": entry.get("kind", "expense"),
            "description": entry.get("description", ""),
            "amount": entry.get("amount", 0),
            "recurrence": entry.get("recurrence", "monthly"),
            "sort_order": sort_order,
        }
        for field, col in (
            ("type", "type"),
            ("date", "date"),
            ("dayOfMonth", "day_of_month"),
            ("month", "month"),
            ("dayOfYear", "day_of_year"),
            ("continuous", "continuous"),
        ):
            val = entry.get(field)
            if val is not None:
                row[col] = val
        ref = entry.get("autoAccountRef")
        if ref is not None and ref in account_id_map:
            row["auto_account_ref"] = account_id_map[ref]
        conn.execute(insert(budget_entries).values(**row))

    # Import asset entries; build yaml_asset_id → db_id map for assetRef rewriting
    asset_id_map: dict[int, int] = {}
    asset_db_ids: list[tuple[int, dict]] = []  # (db_id, original_entry)
    for sort_order, entry in enumerate(data.get("assets") or []):
        row = {
            "snapshot_id": snapshot_id,
            "kind": entry.get("kind", "asset"),
            "name": entry.get("name", ""),
            "sort_order": sort_order,
        }
        for field, col in (
            ("institution", "institution"),
            ("value", "value"),
            ("source", "source"),
            ("quantity", "quantity"),
            ("balance", "balance"),
            ("interestRate", "interest_rate"),
            ("nextDueDate", "next_due_date"),
            ("asOfDate", "as_of_date"),
        ):
            val = entry.get(field)
            if val is not None:
                row[col] = val
        db_id = conn.execute(insert(asset_entries).values(**row)).inserted_primary_key[
            0
        ]
        asset_db_ids.append((db_id, entry))
        yaml_asset_id = entry.get("id")
        if yaml_asset_id is not None:
            asset_id_map[yaml_asset_id] = db_id

    # Rewrite asset_ref for debt entries
    for db_id, entry in asset_db_ids:
        ref = entry.get("assetRef")
        if ref is not None and ref in asset_id_map:
            conn.execute(
                update(asset_entries)
                .where(asset_entries.c.id == db_id)
                .values(asset_ref=asset_id_map[ref])
            )

    conn.commit()
    return snapshot_id


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import YAML finances file(s) into SQLite DB"
    )
    parser.add_argument(
        "path",
        type=Path,
        help="YAML file or directory containing YAML files to import",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("finances.db"),
        help="Path to SQLite database (default: finances.db)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="Snapshot name (only when importing a single file; default: file stem)",
    )
    args = parser.parse_args()

    engine = get_engine(args.db)
    init_db(engine)

    paths: list[Path]
    if args.path.is_dir():
        paths = sorted(p for p in args.path.iterdir() if p.suffix in (".yaml", ".yml"))
        if args.name:
            print(
                "Error: --name can only be used when importing a single file",
                file=sys.stderr,
            )
            return 1
    else:
        paths = [args.path]

    with engine.connect() as conn:
        for p in paths:
            name = args.name if len(paths) == 1 else None
            try:
                snap_id = import_yaml(conn, p, name=name)
                print(
                    f"Imported '{p}' as snapshot id={snap_id} name='{name or p.stem}'"
                )
            except ValueError as e:
                print(f"Error importing '{p}': {e}", file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
