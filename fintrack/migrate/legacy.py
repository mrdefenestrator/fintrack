"""One-time migration of the legacy spending.db and finances.db into a
unified fintrack database.

Driven by a reviewable mapping file rather than interactive prompts: run with
--write-template to scan both legacy databases and emit a YAML mapping with
auto-matched account pairs, review/edit it, then run again with --mapping to
apply. Legacy databases are read by reflection (never through current models)
and are never written to.

Key mechanics:
- finances snapshots/accounts/budget/assets copy over with the schema renames
  (limit -> credit_limit, type -> account_type, string dates -> Date).
- Each spending account either merges into a finances account (they were the
  same real-world account) or is created fresh in a snapshot of your choice.
- Transaction fingerprints embed the account id, so they are recomputed with
  the new ids using the identical algorithm; a self-check first recomputes the
  legacy fingerprints with the old ids to prove the data round-trips.
- balance_history is seeded from confirmed imports' captured ledger balances
  ('statement' rows) and each finances account's manual balance ('migration'
  rows); accounts then re-sync to their latest history row.
"""

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Connection, Engine, MetaData, Table, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from fintrack.core import models
from fintrack.core.config import CATEGORIES_CONFIG
from fintrack.core.db import get_engine, init_db
from fintrack.ledger.importer.dedup import compute_fingerprints
from fintrack.networth.calculations import liquid_minus_cc, liquid_total


class MigrationError(Exception):
    """A condition that must be resolved before the migration can run."""


# ---------------------------------------------------------------------------
# Legacy readers (reflection only — legacy schemas differ from current models)
# ---------------------------------------------------------------------------

_SPENDING_TABLES = (
    "accounts",
    "imports",
    "transactions",
    "merchant_cache",
    "transaction_corrections",
    "categories",
)

_FINANCES_TABLES = (
    "fin_snapshots",
    "fin_accounts",
    "fin_budget_entries",
    "fin_asset_entries",
)


def _reflect(engine: Engine, names: tuple[str, ...]) -> dict[str, Table]:
    md = MetaData()
    try:
        return {n: Table(n, md, autoload_with=engine) for n in names}
    except Exception as e:
        raise MigrationError(f"Could not read legacy database ({engine.url}): {e}")


def _rows(conn: Connection, table: Table, order_by=None) -> list[dict[str, Any]]:
    stmt = select(table)
    if order_by is not None:
        stmt = stmt.order_by(*order_by)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def read_legacy_spending(engine: Engine) -> dict[str, list[dict[str, Any]]]:
    t = _reflect(engine, _SPENDING_TABLES)
    with engine.connect() as conn:
        return {
            "accounts": _rows(conn, t["accounts"], [t["accounts"].c.id]),
            "imports": _rows(conn, t["imports"], [t["imports"].c.id]),
            "transactions": _rows(
                conn,
                t["transactions"],
                [t["transactions"].c.import_id, t["transactions"].c.id],
            ),
            "merchant_cache": _rows(conn, t["merchant_cache"]),
            "transaction_corrections": _rows(conn, t["transaction_corrections"]),
            "categories": _rows(conn, t["categories"], [t["categories"].c.sort_order]),
        }


def read_legacy_finances(engine: Engine) -> dict[str, list[dict[str, Any]]]:
    t = _reflect(engine, _FINANCES_TABLES)
    with engine.connect() as conn:
        return {
            "snapshots": _rows(conn, t["fin_snapshots"], [t["fin_snapshots"].c.id]),
            "accounts": _rows(
                conn,
                t["fin_accounts"],
                [t["fin_accounts"].c.snapshot_id, t["fin_accounts"].c.sort_order],
            ),
            "budget": _rows(
                conn,
                t["fin_budget_entries"],
                [
                    t["fin_budget_entries"].c.snapshot_id,
                    t["fin_budget_entries"].c.sort_order,
                ],
            ),
            "assets": _rows(
                conn,
                t["fin_asset_entries"],
                [
                    t["fin_asset_entries"].c.snapshot_id,
                    t["fin_asset_entries"].c.sort_order,
                ],
            ),
        }


# ---------------------------------------------------------------------------
# Account matching + mapping template
# ---------------------------------------------------------------------------


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().replace(".", "").split())


def _scalar(value: str) -> str:
    """Render a string as a safe YAML scalar (JSON strings are valid YAML)."""
    import json

    return json.dumps(value)


def disambiguate_fin_accounts(
    fin_accounts: list[dict[str, Any]], warnings: list[str] | None = None
) -> None:
    """Set a unique-per-snapshot "final_name" on every finances account row.

    The legacy schema allowed duplicate names within a snapshot (e.g. several
    'Personal Checking' rows differing by institution); the unified schema
    enforces UNIQUE(snapshot_id, name). Every member of a duplicated group
    gets qualified — institution first, then partial account number, then a
    counter — so the group reads consistently.
    """
    groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for a in fin_accounts:
        groups.setdefault((a["snapshot_id"], _norm(a["name"])), []).append(a)
    for members in groups.values():
        if len(members) == 1:
            members[0]["final_name"] = members[0]["name"]
            continue
        used: set[str] = set()
        for i, a in enumerate(members, start=1):
            for candidate in (
                f"{a['name']} ({a['institution']})" if a.get("institution") else None,
                f"{a['name']} [{a['partial_account_number']}]"
                if a.get("partial_account_number")
                else None,
                f"{a['name']} #{i}",
            ):
                if candidate and candidate not in used:
                    a["final_name"] = candidate
                    used.add(candidate)
                    break
        if warnings is not None:
            warnings.append(
                f"duplicate account name '{members[0]['name']}' in a snapshot; "
                f"renamed to: {', '.join(m['final_name'] for m in members)}"
            )


def propose_matches(
    spending_accounts: list[dict[str, Any]],
    fin_accounts: list[dict[str, Any]],
    snapshot_names: dict[int, str],
) -> list[dict[str, Any]]:
    """For each spending account, propose a finances account to merge into.

    Returns entries of the form:
      {"spending": <row>, "match": <fin row> | None, "candidates": [<fin row>...]}
    where candidates holds the ambiguous options when no unique match exists.
    """
    proposals = []
    for sp in spending_accounts:
        # 1. exact normalized-name match (against original or qualified name)
        cands = [
            f
            for f in fin_accounts
            if _norm(sp["name"]) in (_norm(f["name"]), _norm(f["final_name"]))
        ]
        if len(cands) != 1:
            # 2. same institution + partial account number in the spending name,
            #    or containment either way
            by_inst = [
                f
                for f in fin_accounts
                if _norm(f.get("institution")) == _norm(sp.get("institution"))
                and _norm(f.get("institution"))
            ]
            scored = [
                f
                for f in by_inst
                if (
                    f.get("partial_account_number")
                    and str(f["partial_account_number"]) in sp["name"]
                )
                or _norm(f["name"]) in _norm(sp["name"])
                or _norm(sp["name"]) in _norm(f["name"])
            ]
            cands = scored if scored else cands
        proposals.append(
            {
                "spending": sp,
                "match": cands[0] if len(cands) == 1 else None,
                "candidates": cands if len(cands) > 1 else [],
            }
        )
    return proposals


def render_mapping_template(
    spending_db: Path, finances_db: Path, default_snapshot: str | None = None
) -> str:
    """Scan both legacy databases and render the YAML mapping template."""
    sp = read_legacy_spending(get_engine(spending_db))
    fin = read_legacy_finances(get_engine(finances_db))
    snapshot_names = {s["id"]: s["name"] for s in fin["snapshots"]}
    default_snapshot = default_snapshot or (
        fin["snapshots"][0]["name"] if fin["snapshots"] else "default"
    )

    disambiguate_fin_accounts(fin["accounts"])
    proposals = propose_matches(sp["accounts"], fin["accounts"], snapshot_names)

    lines = [
        "# fintrack migrate-legacy account mapping",
        f"# spending: {spending_db}",
        f"# finances: {finances_db}",
        "#",
        "# For each spending account choose ONE of:",
        "#   merge_into: {snapshot: <name>, name: <finances account name>}",
        "#     -> it is the same real-world account as that finances account",
        "#   snapshot: <name>",
        "#     -> no finances counterpart; create it fresh in that snapshot",
        "#",
        f"# finances snapshots: {', '.join(snapshot_names.values()) or '(none)'}",
        "",
        "spending_accounts:",
    ]
    for p in proposals:
        spa = p["spending"]
        lines.append(f"  - name: {_scalar(spa['name'])}")
        if p["match"] is not None:
            m = p["match"]
            snap = snapshot_names[m["snapshot_id"]]
            lines.append("    # auto-matched by name/institution")
            lines.append("    merge_into:")
            lines.append(f"      snapshot: {_scalar(snap)}")
            lines.append(f"      name: {_scalar(m['final_name'])}")
        else:
            if p["candidates"]:
                opts = ", ".join(
                    f"{snapshot_names[c['snapshot_id']]}/{c['final_name']}"
                    for c in p["candidates"]
                )
                lines.append(f"    # ambiguous — candidates: {opts}")
                lines.append("    # replace 'snapshot' with a merge_into if one fits")
            lines.append(f"    snapshot: {_scalar(default_snapshot)}")
    lines.append("")
    return "\n".join(lines)


def load_mapping(path: Path) -> dict[str, dict[str, Any]]:
    """Parse the mapping file into {spending account name: action dict}."""
    data = yaml.safe_load(path.read_text()) or {}
    result: dict[str, dict[str, Any]] = {}
    for entry in data.get("spending_accounts") or []:
        name = entry.get("name")
        if not name:
            raise MigrationError(f"Mapping entry without a name: {entry}")
        merge_into = entry.get("merge_into")
        snapshot = entry.get("snapshot")
        if bool(merge_into) == bool(snapshot):
            raise MigrationError(
                f"Mapping for '{name}' must have exactly one of merge_into/snapshot"
            )
        result[name] = {"merge_into": merge_into, "snapshot": snapshot}
    return result


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _lenient_date(value: Any, context: str, warnings: list[str]) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        warnings.append(f"unparseable date {value!r} in {context}; stored as null")
        return None


def _self_check_fingerprints(sp: dict[str, list[dict[str, Any]]]) -> int:
    """Recompute legacy fingerprints with the ORIGINAL account ids.

    Proves that values read back from the legacy database reproduce the
    fingerprints computed at import time, so recomputing with new account ids
    preserves dedup behavior. Returns the mismatch count.
    """
    mismatches = 0
    by_import: dict[int, list[dict[str, Any]]] = {}
    for t in sp["transactions"]:
        by_import.setdefault(t["import_id"], []).append(t)
    for txns in by_import.values():
        fps = compute_fingerprints(txns, txns[0]["account_id"])
        mismatches += sum(1 for t, fp in zip(txns, fps) if t["fingerprint"] != fp)
    return mismatches


def _assert_target_empty(conn: Connection) -> None:
    for table in (models.snapshots, models.accounts, models.transactions):
        count = conn.execute(select(func.count()).select_from(table)).scalar()
        if count:
            raise MigrationError(
                f"Target database is not empty ({table.name} has {count} rows); "
                "migrate-legacy requires a fresh database"
            )


def apply_migration(
    spending_db: Path,
    finances_db: Path,
    mapping: dict[str, dict[str, Any]],
    target_db: Path,
    dry_run: bool = False,
) -> str:
    """Run the migration; returns a human-readable report.

    dry_run performs every insert in a transaction and rolls back (the empty
    target schema itself is still created).
    """
    warnings: list[str] = []
    sp = read_legacy_spending(get_engine(spending_db))
    fin = read_legacy_finances(get_engine(finances_db))
    snapshot_names = {s["id"]: s["name"] for s in fin["snapshots"]}

    missing = [a["name"] for a in sp["accounts"] if a["name"] not in mapping]
    if missing:
        raise MigrationError(
            f"Mapping has no entry for spending account(s): {', '.join(missing)}; "
            "regenerate with --write-template or add them"
        )

    fp_mismatches = _self_check_fingerprints(sp)
    if fp_mismatches:
        warnings.append(
            f"{fp_mismatches} legacy fingerprints did not reproduce from stored "
            "values; dedup against re-imports of old files may miss for those rows"
        )

    engine = get_engine(target_db)
    init_db(engine)

    conn = engine.connect()
    try:
        _assert_target_empty(conn)

        # --- snapshots ---------------------------------------------------
        snap_map: dict[int, int] = {}
        snap_by_name: dict[str, int] = {}
        for s in fin["snapshots"]:
            new_id = conn.execute(
                insert(models.snapshots).values(
                    name=s["name"], created_at=s.get("created_at")
                )
            ).inserted_primary_key[0]
            snap_map[s["id"]] = new_id
            snap_by_name[s["name"]] = new_id

        def snapshot_id_for(name: str) -> int:
            if name not in snap_by_name:
                snap_by_name[name] = conn.execute(
                    insert(models.snapshots).values(name=name)
                ).inserted_primary_key[0]
                warnings.append(f"created snapshot '{name}' (not in finances db)")
            return snap_by_name[name]

        # --- finances accounts -------------------------------------------
        disambiguate_fin_accounts(fin["accounts"], warnings)
        fin_map: dict[int, int] = {}
        unified_by_key: dict[tuple[str, str], int] = {}  # (snapshot, name) -> id
        for a in fin["accounts"]:
            balance = a.get("balance")
            if (
                a["type"] == "credit_card"
                and balance is None
                and a.get("available") is not None
                and a.get("limit") is not None
            ):
                balance = a["available"] - a["limit"]
            # partial_account_number is a dropped column on the unified schema;
            # fold it into the stored name (unless already present, e.g. the
            # disambiguation pass above already used it to qualify the name)
            # so the information isn't silently lost. unified_by_key below
            # stays keyed by the pre-fold final_name, since that's what the
            # mapping YAML (from render_mapping_template) references.
            stored_name = a["final_name"]
            partial = a.get("partial_account_number")
            if partial and str(partial) not in stored_name:
                stored_name = f"{stored_name} [{partial}]"
            new_id = conn.execute(
                insert(models.accounts).values(
                    snapshot_id=snap_map[a["snapshot_id"]],
                    name=stored_name,
                    institution=a.get("institution"),
                    account_type=a["type"],
                    balance=balance,
                    credit_limit=a.get("limit"),
                    available=a.get("available"),
                    rewards_balance=a.get("rewards_balance"),
                    statement_balance=a.get("statement_balance"),
                    statement_due_day_of_month=a.get("statement_due_day_of_month"),
                    as_of_date=_lenient_date(
                        a.get("as_of_date"), f"account '{a['name']}'", warnings
                    ),
                    minimum_balance=a.get("minimum_balance"),
                    sort_order=a.get("sort_order") or 0,
                )
            ).inserted_primary_key[0]
            fin_map[a["id"]] = new_id
            unified_by_key[(snapshot_names[a["snapshot_id"]], a["final_name"])] = new_id
        for a in fin["accounts"]:  # second pass: self-references
            ref = a.get("payment_account_ref")
            if ref is not None and ref in fin_map:
                conn.execute(
                    update(models.accounts)
                    .where(models.accounts.c.id == fin_map[a["id"]])
                    .values(payment_account_ref=fin_map[ref])
                )

        # --- spending accounts (merge or create per mapping) --------------
        sp_map: dict[int, int] = {}
        merged_count = created_count = 0
        for a in sp["accounts"]:
            action = mapping[a["name"]]
            if action["merge_into"]:
                key = (action["merge_into"]["snapshot"], action["merge_into"]["name"])
                if key not in unified_by_key:
                    raise MigrationError(
                        f"merge_into target {key[0]}/{key[1]} for spending account "
                        f"'{a['name']}' does not exist in the finances database"
                    )
                uid = unified_by_key[key]
                sp_map[a["id"]] = uid
                merged_count += 1
                row = (
                    conn.execute(
                        select(models.accounts).where(models.accounts.c.id == uid)
                    )
                    .mappings()
                    .one()
                )
                if row["institution"] is None and a.get("institution"):
                    conn.execute(
                        update(models.accounts)
                        .where(models.accounts.c.id == uid)
                        .values(institution=a["institution"])
                    )
                if row["account_type"] != a["account_type"]:
                    warnings.append(
                        f"account '{a['name']}': spending type "
                        f"'{a['account_type']}' differs from finances type "
                        f"'{row['account_type']}'; kept finances type"
                    )
            else:
                if (action["snapshot"], a["name"]) in unified_by_key:
                    raise MigrationError(
                        f"spending account '{a['name']}' would collide with an "
                        f"existing account in snapshot '{action['snapshot']}'; "
                        "use merge_into if they are the same account"
                    )
                sid = snapshot_id_for(action["snapshot"])
                next_order = (
                    conn.execute(
                        select(func.max(models.accounts.c.sort_order)).where(
                            models.accounts.c.snapshot_id == sid
                        )
                    ).scalar()
                    or 0
                ) + 1
                sp_map[a["id"]] = conn.execute(
                    insert(models.accounts).values(
                        snapshot_id=sid,
                        name=a["name"],
                        institution=a.get("institution"),
                        account_type=a["account_type"],
                        sort_order=next_order,
                        created_at=a.get("created_at"),
                    )
                ).inserted_primary_key[0]
                created_count += 1

        # --- categories (seed config ∪ legacy) ----------------------------
        # Seeded inline rather than via seed_categories(), which commits and
        # would break --dry-run's single-transaction rollback.
        seed = yaml.safe_load(CATEGORIES_CONFIG.read_text())["categories"]
        existing: set[str] = set()
        next_sort = 0
        for cat in seed:
            conn.execute(
                insert(models.categories).values(
                    name=cat["name"], sort_order=cat["sort_order"]
                )
            )
            existing.add(cat["name"])
            next_sort = max(next_sort, cat["sort_order"])
        for c in sp["categories"]:
            if c["name"] not in existing:
                next_sort += 1
                conn.execute(
                    insert(models.categories).values(
                        name=c["name"], sort_order=next_sort
                    )
                )
                existing.add(c["name"])

        # --- merchant cache ------------------------------------------------
        for m in sp["merchant_cache"]:
            conn.execute(
                insert(models.merchant_cache).values(
                    merchant_name=m["merchant_name"],
                    category=m["category"],
                    source=m["source"],
                    created_at=m.get("created_at"),
                    updated_at=m.get("updated_at"),
                )
            )

        # --- imports --------------------------------------------------------
        imp_map: dict[int, int] = {}
        for i in sp["imports"]:
            imp_map[i["id"]] = conn.execute(
                insert(models.imports).values(
                    account_id=sp_map[i["account_id"]],
                    filename=i["filename"],
                    file_hash=i["file_hash"],
                    imported_at=i.get("imported_at"),
                    status=i["status"],
                    ledger_balance=i.get("ledger_balance"),
                    ledger_balance_date=i.get("ledger_balance_date"),
                    available_balance=i.get("available_balance"),
                    available_balance_date=i.get("available_balance_date"),
                    beginning_balance=i.get("beginning_balance"),
                )
            ).inserted_primary_key[0]

        # --- transactions (fingerprints recomputed with new account ids) ----
        txn_map: dict[int, int] = {}
        by_import: dict[int, list[dict[str, Any]]] = {}
        for t in sp["transactions"]:
            by_import.setdefault(t["import_id"], []).append(t)
        for old_import_id, txns in by_import.items():
            new_account_id = sp_map[txns[0]["account_id"]]
            fps = compute_fingerprints(txns, new_account_id)
            for t, fp in zip(txns, fps):
                txn_map[t["id"]] = conn.execute(
                    insert(models.transactions).values(
                        import_id=imp_map[old_import_id],
                        account_id=new_account_id,
                        date=t["date"],
                        amount=t["amount"],
                        raw_description=t["raw_description"],
                        normalized_merchant=t["normalized_merchant"],
                        fingerprint=fp,
                        created_at=t.get("created_at"),
                    )
                ).inserted_primary_key[0]

        # --- corrections -----------------------------------------------------
        orphaned_corrections = 0
        for c in sp["transaction_corrections"]:
            if c["transaction_id"] not in txn_map:
                # Legacy spending ran without FK enforcement; reject_import
                # deleted transactions but left their corrections behind.
                orphaned_corrections += 1
                continue
            conn.execute(
                insert(models.transaction_corrections).values(
                    transaction_id=txn_map[c["transaction_id"]],
                    category=c.get("category"),
                    merchant_name=c.get("merchant_name"),
                    notes=c.get("notes"),
                    created_at=c.get("created_at"),
                )
            )
        if orphaned_corrections:
            warnings.append(
                f"skipped {orphaned_corrections} correction(s) whose transaction "
                "no longer exists in the legacy database (orphans from the "
                "FK-off era)"
            )

        # --- budget entries ---------------------------------------------------
        for b in fin["budget"]:
            ref = b.get("auto_account_ref")
            conn.execute(
                insert(models.budget_entries).values(
                    snapshot_id=snap_map[b["snapshot_id"]],
                    kind=b["kind"],
                    description=b["description"],
                    amount=b["amount"],
                    recurrence=b["recurrence"],
                    type=b.get("type"),
                    date=_lenient_date(
                        b.get("date"), f"budget '{b['description']}'", warnings
                    ),
                    day_of_month=b.get("day_of_month"),
                    month=b.get("month"),
                    day_of_year=b.get("day_of_year"),
                    continuous=b.get("continuous"),
                    auto_account_ref=fin_map[ref] if ref is not None else None,
                    sort_order=b.get("sort_order") or 0,
                )
            )

        # --- asset entries -----------------------------------------------------
        asset_map: dict[int, int] = {}
        for a in fin["assets"]:
            legacy_due_date = _lenient_date(
                a.get("next_due_date"), f"asset '{a['name']}'", warnings
            )
            asset_map[a["id"]] = conn.execute(
                insert(models.asset_entries).values(
                    snapshot_id=snap_map[a["snapshot_id"]],
                    kind=a["kind"],
                    name=a["name"],
                    institution=a.get("institution"),
                    value=a.get("value"),
                    source=a.get("source"),
                    quantity=a.get("quantity"),
                    balance=a.get("balance"),
                    interest_rate=a.get("interest_rate"),
                    statement_due_day_of_month=(
                        legacy_due_date.day if legacy_due_date else None
                    ),
                    as_of_date=_lenient_date(
                        a.get("as_of_date"), f"asset '{a['name']}'", warnings
                    ),
                    sort_order=a.get("sort_order") or 0,
                )
            ).inserted_primary_key[0]
        for a in fin["assets"]:  # second pass: debt -> asset references
            ref = a.get("asset_ref")
            if ref is not None and ref in asset_map:
                conn.execute(
                    update(models.asset_entries)
                    .where(models.asset_entries.c.id == asset_map[a["id"]])
                    .values(asset_ref=asset_map[ref])
                )

        # --- balance history ------------------------------------------------
        statement_rows = 0
        for i in sp["imports"]:
            if i["status"] != "confirmed" or i.get("ledger_balance") is None:
                continue
            as_of = i.get("ledger_balance_date") or (
                i["imported_at"].date() if i.get("imported_at") else date.today()
            )
            stmt = (
                sqlite_insert(models.balance_history)
                .values(
                    account_id=sp_map[i["account_id"]],
                    as_of=as_of,
                    balance=i["ledger_balance"],
                    available=i.get("available_balance"),
                    source="statement",
                    import_id=imp_map[i["id"]],
                )
                .on_conflict_do_update(
                    index_elements=["account_id", "as_of", "source"],
                    set_={
                        "balance": i["ledger_balance"],
                        "available": i.get("available_balance"),
                        "import_id": imp_map[i["id"]],
                    },
                )
            )
            conn.execute(stmt)
            statement_rows += 1
        migration_rows = 0
        for a in fin["accounts"]:
            if a.get("balance") is None and not (
                a["type"] == "credit_card"
                and a.get("available") is not None
                and a.get("limit") is not None
            ):
                continue
            balance = (
                a["balance"]
                if a.get("balance") is not None
                else a["available"] - a["limit"]
            )
            as_of = _lenient_date(a.get("as_of_date"), f"account '{a['name']}'", [])
            if as_of is None:
                as_of = date.today()
            conn.execute(
                insert(models.balance_history).values(
                    account_id=fin_map[a["id"]],
                    as_of=as_of,
                    balance=balance,
                    source="migration",
                )
            )
            migration_rows += 1

        # --- re-sync account balances from latest history --------------------
        bh = models.balance_history
        account_ids = {
            r[0] for r in conn.execute(select(bh.c.account_id).distinct()).all()
        }
        for aid in account_ids:
            latest = (
                conn.execute(
                    select(bh)
                    .where(bh.c.account_id == aid)
                    .order_by(bh.c.as_of.desc(), bh.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .one()
            )
            conn.execute(
                update(models.accounts)
                .where(models.accounts.c.id == aid)
                .values(balance=latest["balance"], as_of_date=latest["as_of"])
            )

        # --- verify -----------------------------------------------------------
        report = _build_report(
            conn,
            sp,
            fin,
            snap_map,
            merged_count,
            created_count,
            statement_rows,
            migration_rows,
            fp_mismatches,
            warnings,
            dry_run,
        )

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
            fk_issues = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if fk_issues:
                raise MigrationError(f"foreign_key_check failed: {fk_issues[:5]}")
        return report
    finally:
        conn.close()


def _count(conn: Connection, table: Table) -> int:
    return conn.execute(select(func.count()).select_from(table)).scalar()


def _build_report(
    conn: Connection,
    sp: dict[str, list[dict[str, Any]]],
    fin: dict[str, list[dict[str, Any]]],
    snap_map: dict[int, int],
    merged_count: int,
    created_count: int,
    statement_rows: int,
    migration_rows: int,
    fp_mismatches: int,
    warnings: list[str],
    dry_run: bool,
) -> str:
    lines = [
        "=== migrate-legacy report" + (" (DRY RUN — rolled back)" if dry_run else "")
    ]
    lines.append("")
    lines.append("Row counts (legacy -> unified):")
    for label, legacy_count, table in (
        ("snapshots", len(fin["snapshots"]), models.snapshots),
        (
            "accounts",
            f"{len(fin['accounts'])} fin + {len(sp['accounts'])} spending",
            models.accounts,
        ),
        ("imports", len(sp["imports"]), models.imports),
        ("transactions", len(sp["transactions"]), models.transactions),
        ("merchant_cache", len(sp["merchant_cache"]), models.merchant_cache),
        (
            "corrections",
            len(sp["transaction_corrections"]),
            models.transaction_corrections,
        ),
        ("budget_entries", len(fin["budget"]), models.budget_entries),
        ("asset_entries", len(fin["assets"]), models.asset_entries),
    ):
        lines.append(f"  {label:16} {legacy_count} -> {_count(conn, table)}")
    lines.append(
        f"  accounts merged: {merged_count}; created from spending: {created_count}"
    )
    lines.append(
        f"  balance_history: {statement_rows} statement + {migration_rows} migration rows"
    )
    lines.append(
        "  fingerprint self-check: "
        + ("OK (all reproduce)" if not fp_mismatches else f"{fp_mismatches} MISMATCHES")
    )

    lines.append("")
    lines.append("Liquid totals per snapshot (legacy finances -> unified):")
    from fintrack.accounts.repository import get_accounts

    snapshot_names = {s["id"]: s["name"] for s in fin["snapshots"]}
    for old_id, new_id in snap_map.items():
        legacy_accounts = [
            {
                "type": a["type"],
                "balance": a.get("balance"),
                "limit": a.get("limit"),
                "available": a.get("available"),
                "rewards_balance": a.get("rewards_balance"),
            }
            for a in fin["accounts"]
            if a["snapshot_id"] == old_id
        ]
        new_accounts = get_accounts(conn, new_id)
        old_liq, new_liq = liquid_total(legacy_accounts), liquid_total(new_accounts)
        old_net, new_net = (
            liquid_minus_cc(legacy_accounts),
            liquid_minus_cc(new_accounts),
        )
        flag = "" if (old_liq == new_liq and old_net == new_net) else "  <— differs"
        lines.append(
            f"  {snapshot_names[old_id]:12} liquid {old_liq} -> {new_liq}; "
            f"accounts-total {old_net} -> {new_net}{flag}"
        )
    lines.append(
        "  (differences are expected where a statement balance is newer than the"
    )
    lines.append("   manually entered finances balance)")

    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  - {w}" for w in warnings)
    return "\n".join(lines)
