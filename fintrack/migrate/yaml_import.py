"""Import a YAML finances file into the SQLite database as a named snapshot.

Usage:
    uv run python -m fintrack.migrate.yaml_import data/mike.yaml [--db finances.db] [--name mike]
    uv run python -m fintrack.migrate.yaml_import data/  # import all YAML files in a directory
"""

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Connection, insert, select, update

from fintrack.core.coerce import to_date
from fintrack.core.db import get_engine, init_db
from fintrack.core.holdings import DETAIL_TABLES, insert_holding
from fintrack.core.models import budget_entries, loan_details, snapshots
from fintrack.core.types import group_for_account_type


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
    account_groups: dict[int, str] = {}  # db_id -> group (for the ref pass)
    sort_orders: dict[str, int] = {}
    _ACCOUNT_DETAIL_FIELDS = {
        "cash": (("balance", "balance"), ("minimum_balance", "minimum_balance")),
        "credit_card": (
            ("balance", "balance"),
            ("limit", "credit_limit"),
            ("rewards_balance", "rewards_balance"),
            ("statement_balance", "statement_balance"),
            ("statement_due_day_of_month", "statement_due_day_of_month"),
        ),
        "loan": (
            ("balance", "balance"),
            ("statement_due_day_of_month", "statement_due_day_of_month"),
        ),
    }
    for acc in data.get("accounts") or []:
        yaml_id = acc.get("id")
        account_type = acc.get("type", "checking")
        group = group_for_account_type(account_type)
        spine: dict[str, Any] = {"type": account_type, "name": acc.get("name", "")}
        if acc.get("institution") is not None:
            spine["institution"] = acc["institution"]
        if acc.get("asOfDate") is not None:
            spine["as_of_date"] = to_date(acc["asOfDate"])
        detail: dict[str, Any] = {}
        for field, col in _ACCOUNT_DETAIL_FIELDS[group]:
            val = acc.get(field)
            if val is not None:
                detail[col] = val
        # Canonical signed balance for credit cards (negative = owed)
        if (
            group == "credit_card"
            and detail.get("balance") is None
            and acc.get("available") is not None
            and detail.get("credit_limit") is not None
        ):
            detail["balance"] = acc["available"] - detail["credit_limit"]
        order = sort_orders.get(group, 0)
        sort_orders[group] = order + 1
        db_id = insert_holding(
            conn, snapshot_id, group, spine, detail, sort_order=order
        )
        account_groups[db_id] = group
        if yaml_id is not None:
            account_id_map[yaml_id] = db_id

    # Rewrite payment_account_ref after all accounts are inserted
    for acc in data.get("accounts") or []:
        yaml_id = acc.get("id")
        ref = acc.get("paymentAccountRef")
        if yaml_id is not None and ref is not None and ref in account_id_map:
            db_id = account_id_map[yaml_id]
            detail = DETAIL_TABLES[account_groups[db_id]]
            if "payment_account_ref" in detail.c:
                conn.execute(
                    update(detail)
                    .where(detail.c.holding_id == db_id)
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
            ("category", "category"),
            ("date", "date"),
            ("dayOfMonth", "day_of_month"),
            ("month", "month"),
            ("dayOfYear", "day_of_year"),
            ("continuous", "continuous"),
        ):
            val = entry.get(field)
            if val is not None:
                row[col] = to_date(val) if col == "date" else val
        ref = entry.get("autoAccountRef")
        if ref is not None and ref in account_id_map:
            row["auto_account_ref"] = account_id_map[ref]
        conn.execute(insert(budget_entries).values(**row))

    # Import asset entries; build yaml_asset_id → db_id map for assetRef rewriting
    asset_id_map: dict[int, int] = {}
    asset_db_ids: list[tuple[int, dict]] = []  # (db_id, original_entry)
    for entry in data.get("assets") or []:
        is_debt = entry.get("kind") == "debt"
        group = "loan" if is_debt else "asset"
        spine: dict[str, Any] = {"name": entry.get("name", "")}
        if is_debt:
            spine["type"] = "loan"
        elif entry.get("type") is not None:
            spine["type"] = entry["type"]
        if entry.get("institution") is not None:
            spine["institution"] = entry["institution"]
        if entry.get("asOfDate") is not None:
            spine["as_of_date"] = to_date(entry["asOfDate"])
        detail: dict[str, Any] = {}
        if is_debt:
            for field, col in (
                ("interestRate", "interest_rate"),
                ("originalPrincipal", "original_principal"),
                ("termMonths", "term_months"),
                ("originationDate", "origination_date"),
                ("statement_due_day_of_month", "statement_due_day_of_month"),
            ):
                val = entry.get(field)
                if val is not None:
                    detail[col] = to_date(val) if col == "origination_date" else val
            # Backward compatibility for pre-origination YAML exports.
            if (
                detail.get("statement_due_day_of_month") is None
                and entry.get("nextDueDate") is not None
            ):
                detail["statement_due_day_of_month"] = to_date(entry["nextDueDate"]).day
            # Debts fold any quantity into the total owed, stored signed
            # (negative = owed) — docs/notes-schema-split.md D1/D2.
            if entry.get("balance") is not None:
                balance = entry["balance"]
                if entry.get("quantity") is not None:
                    balance = balance * entry["quantity"]
                detail["balance"] = -balance
        else:
            for field, col in (
                ("value", "value"),
                ("source", "source"),
                ("quantity", "quantity"),
            ):
                val = entry.get(field)
                if val is not None:
                    detail[col] = val
        order = sort_orders.get(group, 0)
        sort_orders[group] = order + 1
        db_id = insert_holding(
            conn, snapshot_id, group, spine, detail, sort_order=order
        )
        asset_db_ids.append((db_id, entry))
        yaml_asset_id = entry.get("id")
        if yaml_asset_id is not None:
            asset_id_map[yaml_asset_id] = db_id

    # Rewrite asset_ref for debt entries
    for db_id, entry in asset_db_ids:
        ref = entry.get("assetRef")
        if ref is not None and ref in asset_id_map:
            conn.execute(
                update(loan_details)
                .where(loan_details.c.holding_id == db_id)
                .values(secured_asset_ref=asset_id_map[ref])
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
