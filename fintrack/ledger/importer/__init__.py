from datetime import timedelta
from pathlib import Path

from sqlalchemy import Connection

from fintrack.core.config import INSTITUTIONS_DIR
from fintrack.core.types import ParsedTransaction
from fintrack.ledger.importer.csv_parser import detect_institution_config, parse_csv
from fintrack.ledger.importer.dedup import (
    compute_fingerprints,
    deduplicate,
    first_occurrence_fingerprint,
)
from fintrack.ledger.importer.near_dup import DATE_WINDOW_DAYS, find_near_duplicates
from fintrack.ledger.importer.normalize import normalize_merchant
from fintrack.ledger.importer.ofx import parse_ofx
from fintrack.ledger.repository.duplicates import (
    add_duplicate_flags,
    get_near_dup_candidates,
)
from fintrack.ledger.repository.imports import (
    check_file_hash,
    compute_file_hash,
    create_import,
    get_existing_external_ids,
    get_existing_fingerprints,
    get_transaction_ids_by_fingerprint,
    insert_transactions,
)


def _error(msg: str) -> dict:
    return {
        "import_id": None,
        "new_count": 0,
        "skipped_count": 0,
        "flagged_count": 0,
        "new_merchants": [],
        "error": msg,
    }


def _drop_known_ids(
    txns: list[ParsedTransaction], fps: list[str], known: set[str]
) -> tuple[list[ParsedTransaction], list[str]]:
    kept = [(t, fp) for t, fp in zip(txns, fps) if t.get("external_id") not in known]
    return [t for t, _ in kept], [fp for _, fp in kept]


def _near_duplicates(
    conn: Connection,
    account_id: int,
    parsed: list[ParsedTransaction],
    parsed_fps: list[str],
    to_insert: list[ParsedTransaction],
    to_insert_fps: list[str],
) -> dict[int, tuple[int, str]]:
    """Map index-into-to_insert -> (existing transaction id, reason) for rows
    that look like a copy of something already on file (issue #82).

    Existing rows an exact match already accounts for (a skipped row with the
    same fingerprint or bank id) are left out, so they can't also absorb a
    near-duplicate.
    """
    if not to_insert:
        return {}
    # Whole-file fingerprints are unique within a file, so they identify rows.
    inserted = set(to_insert_fps)
    skipped = [(t, fp) for t, fp in zip(parsed, parsed_fps) if fp not in inserted]
    used_fps = {fp for _, fp in skipped}
    used_ids = {t.get("external_id") for t, _ in skipped} - {None}

    window = timedelta(days=DATE_WINDOW_DAYS)
    existing = [
        row
        for row in get_near_dup_candidates(
            conn,
            account_id,
            min(t["date"] for t in to_insert) - window,
            max(t["date"] for t in to_insert) + window,
        )
        if row["fingerprint"] not in used_fps and row["external_id"] not in used_ids
    ]
    return {
        m.incoming: (existing[m.existing]["id"], m.reason)
        for m in find_near_duplicates(to_insert, existing)
    }


def run_import(
    conn: Connection,
    file_path: str | Path,
    account_id: int,
    configs_dir: str | None = None,
) -> dict:
    """Run the full import pipeline for a single file.

    Returns a dict with keys: import_id, new_count, skipped_count,
    flagged_count, new_merchants.
    """
    file_path = Path(file_path)

    file_hash = compute_file_hash(file_path)
    if check_file_hash(conn, file_hash):
        return _error(f"File already imported: {file_path.name}")

    suffix = file_path.suffix.lower()
    if suffix in (".ofx", ".qfx"):
        result = parse_ofx(file_path)
    elif suffix == ".csv":
        config_path = detect_institution_config(
            file_path, configs_dir or str(INSTITUTIONS_DIR)
        )
        if config_path is None:
            return _error(f"No institution config matches: {file_path.name}")
        result = parse_csv(file_path, config_path)
    else:
        return _error(f"Unsupported format: {suffix}")

    if not result["transactions"]:
        return _error("No transactions found in file")

    # Normalize merchant names
    for txn in result["transactions"]:
        txn["normalized_merchant"] = normalize_merchant(txn["raw_description"])

    # Fingerprint and dedup
    fingerprints = compute_fingerprints(result["transactions"], account_id)
    existing_fps = get_existing_fingerprints(conn, account_id)
    new_txns, new_fps, flagged, flagged_fps = deduplicate(
        result["transactions"], fingerprints, existing_fps, account_id
    )
    # A bank transaction id already on file is a definite duplicate however
    # the description changed between exports (issue #82). Applied after the
    # fingerprint pass so the whole-file sequence numbering stays intact.
    existing_ids = get_existing_external_ids(conn, account_id)
    if existing_ids:
        new_txns, new_fps = _drop_known_ids(new_txns, new_fps, existing_ids)
        flagged, flagged_fps = _drop_known_ids(flagged, flagged_fps, existing_ids)

    skipped_count = len(result["transactions"]) - len(new_txns) - len(flagged)

    # Create import record
    import_id = create_import(
        conn,
        account_id=account_id,
        filename=file_path.name,
        file_hash=file_hash,
        ledger_balance=result.get("ledger_balance"),
        ledger_balance_date=result.get("ledger_balance_date"),
        available_balance=result.get("available_balance"),
        available_balance_date=result.get("available_balance_date"),
        beginning_balance=result.get("beginning_balance"),
    )

    # Same-day repeats (flagged by deduplicate) are inserted too and reviewed
    # in staging. Reuse the whole-file fingerprints from deduplicate() so a
    # re-import compares against a consistent sequence basis.
    to_insert = new_txns + flagged
    to_insert_fps = new_fps + flagged_fps
    near_dups = _near_duplicates(
        conn, account_id, result["transactions"], fingerprints, to_insert, to_insert_fps
    )

    txn_ids = []
    if to_insert:
        txn_ids = insert_transactions(
            conn,
            import_id=import_id,
            account_id=account_id,
            transactions_data=[
                {
                    "date": txn["date"],
                    "amount": txn["amount"],
                    "raw_description": txn["raw_description"],
                    "normalized_merchant": txn["normalized_merchant"],
                    "fingerprint": fp,
                    "external_id": txn.get("external_id"),
                }
                for txn, fp in zip(to_insert, to_insert_fps)
            ],
        )

    flags = {txn_ids[i]: (dup_of, reason) for i, (dup_of, reason) in near_dups.items()}
    # A same-day repeat the matcher didn't pair still gets reviewed against its
    # first-occurrence sibling, as before.
    repeats = [
        (len(new_txns) + k, first_occurrence_fingerprint(txn, account_id))
        for k, txn in enumerate(flagged)
        if txn_ids[len(new_txns) + k] not in flags
    ]
    siblings = get_transaction_ids_by_fingerprint(
        conn, account_id, {fp for _, fp in repeats}
    )
    for i, fp in repeats:
        if fp in siblings:
            flags[txn_ids[i]] = (
                siblings[fp],
                "repeat of an identical same-day charge already on file",
            )
    add_duplicate_flags(
        conn,
        [(txn_id, dup_of, reason) for txn_id, (dup_of, reason) in flags.items()],
        source="import",
    )

    # Collect new merchant names
    new_merchants = list({txn["normalized_merchant"] for txn in to_insert})

    return {
        "import_id": import_id,
        "new_count": len(to_insert) - len(flags),
        "skipped_count": skipped_count,
        "flagged_count": len(flags),
        "new_merchants": new_merchants,
    }
