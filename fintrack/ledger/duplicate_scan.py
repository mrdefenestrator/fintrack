"""Find possible duplicates already in the database (issue #82).

Import-time flagging only protects new imports; copies that got in earlier
(before bank ids and near-duplicate matching) need a scan. The scan replays
import-time matching over confirmed data: per account, imports in the order
they arrived, each import's rows paired against the rows of earlier imports
with the same matcher and the same rules.

Rows that already carry a flag — open, resolved as a duplicate, or dismissed
as not one — are never re-flagged, so dismissals stick and re-running the
scan is idempotent.
"""

from itertools import groupby

from sqlalchemy import Connection

from fintrack.ledger.importer.near_dup import find_near_duplicates
from fintrack.ledger.repository.duplicates import add_duplicate_flags, get_scan_rows

# A row in these states can't be anyone's original (mirrors import time).
_CLAIMED = ("flagged", "duplicate")


def scan_for_duplicates(
    conn: Connection, snapshot_id: int | None = None, *, apply: bool = False
) -> list[dict]:
    """Return the new flags a scan finds, as dicts with the copy (`txn`), the
    original (`of`) and the `reason`; with apply, also record them
    (source='scan')."""
    found: list[dict] = []
    rows = get_scan_rows(conn, snapshot_id)
    for _, account_rows in groupby(rows, key=lambda r: r["account_id"]):
        earlier: list[dict] = []
        for _, batch in groupby(account_rows, key=lambda r: r["import_id"]):
            batch = list(batch)
            incoming = [r for r in batch if r["dup_status"] is None]
            originals = [r for r in earlier if r["dup_status"] not in _CLAIMED]
            for m in find_near_duplicates(incoming, originals):
                txn, of = incoming[m.incoming], originals[m.existing]
                txn["dup_status"] = "flagged"
                found.append({"txn": txn, "of": of, "reason": m.reason})
            earlier.extend(batch)
    if apply:
        add_duplicate_flags(
            conn,
            [(f["txn"]["id"], f["of"]["id"], f["reason"]) for f in found],
            source="scan",
        )
    return found
