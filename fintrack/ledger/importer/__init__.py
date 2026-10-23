from pathlib import Path

from sqlalchemy import Connection

from fintrack.core.config import INSTITUTIONS_DIR
from fintrack.core.types import ParsedTransaction
from fintrack.ledger.importer.csv_parser import detect_institution_config, parse_csv
from fintrack.ledger.importer.dedup import compute_fingerprints, deduplicate
from fintrack.ledger.importer.normalize import normalize_merchant
from fintrack.ledger.importer.ofx import parse_ofx
from fintrack.ledger.repository.imports import (
    check_file_hash,
    compute_file_hash,
    create_import,
    get_existing_external_ids,
    get_existing_fingerprints,
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

    # Build transaction records with fingerprints
    txn_records = []
    for txn, fp in zip(new_txns, new_fps):
        txn_records.append(
            {
                "date": txn["date"],
                "amount": txn["amount"],
                "raw_description": txn["raw_description"],
                "normalized_merchant": txn["normalized_merchant"],
                "fingerprint": fp,
                "external_id": txn.get("external_id"),
            }
        )

    # Insert flagged transactions too (they'll be reviewed in staging).
    # Reuse the whole-file fingerprints from deduplicate() so a re-import
    # compares against a consistent sequence basis.
    for txn, fp in zip(flagged, flagged_fps):
        txn_records.append(
            {
                "date": txn["date"],
                "amount": txn["amount"],
                "raw_description": txn["raw_description"],
                "normalized_merchant": txn["normalized_merchant"],
                "fingerprint": fp,
                "external_id": txn.get("external_id"),
            }
        )

    if txn_records:
        insert_transactions(
            conn,
            import_id=import_id,
            account_id=account_id,
            transactions_data=txn_records,
        )

    # Collect new merchant names
    new_merchants = list({txn["normalized_merchant"] for txn in new_txns + flagged})

    return {
        "import_id": import_id,
        "new_count": len(new_txns),
        "skipped_count": skipped_count,
        "flagged_count": len(flagged),
        "new_merchants": new_merchants,
    }
