"""recompute transaction fingerprints and remove duplicates

Revision ID: f1e2d3c4b5a6
Revises: a7b8c9d0e1f2
Create Date: 2026-09-04 00:00:00.000000

The holdings split (a7b8c9d0e1f2) reassigned every holding a new id and
remapped transactions.account_id onto it, but copied each transaction's
fingerprint verbatim. Fingerprints embed the account id
(fintrack/ledger/importer/dedup.py), so after that migration a re-imported
statement recomputes fingerprints against the *new* holding id and never
matches the stored (old-id) fingerprints -- duplicate detection silently
failed and overlapping re-imports double-counted spending.

This migration repairs both halves:

1. Recompute every transaction's fingerprint from its own fields, keyed by
   the current account_id, with a per-import occurrence sequence (matching
   import-time dedup, which counts repeats within a single file). Amounts are
   read through a typed Numeric(12,2) column so they canonicalize to two
   decimals -- identical to importer.dedup.canonical_amount -- which also
   removes a latent OFX-float fragility, so this recompute runs on *every*
   database (fresh or split-migrated), not just broken ones.

2. Remove duplicates the bug already created. After step 1, two rows are the
   same transaction iff they share a fingerprint, so collapse each fingerprint
   to one row -- exactly what import-time dedup would have done. The surviving
   row is the one carrying a user correction (else the lowest id); corrections
   on removed rows are deleted explicitly (not left to FK-cascade state).

Non-destructive for step 1; step 2 deletes rows, so the SQLite file is copied
to <db>.pre-dedup-fix.bak first. downgrade() raises (irreversible).
"""

import hashlib
import shutil
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1e2d3c4b5a6"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CENTS = Decimal("0.01")

# Lightweight typed view so amount/date round-trip as Decimal/date, matching
# what importer.dedup produces at import time.
_txns = sa.table(
    "transactions",
    sa.column("id", sa.Integer),
    sa.column("import_id", sa.Integer),
    sa.column("account_id", sa.Integer),
    sa.column("date", sa.Date),
    sa.column("amount", sa.Numeric(12, 2)),
    sa.column("raw_description", sa.String),
)


def _backup_db(bind) -> None:
    """Copy the SQLite file to <db>.pre-dedup-fix.bak (skip :memory:)."""
    url = bind.engine.url
    if url.database in (None, ":memory:"):
        return
    src = Path(url.database)
    if src.exists():
        shutil.copy2(src, src.with_suffix(src.suffix + ".pre-dedup-fix.bak"))


def _fingerprint(date_iso: str, amount: str, raw_description: str, account_id: int, seq: int) -> str:
    base_key = f"{date_iso}|{amount}|{raw_description}|{account_id}"
    return hashlib.sha256(f"{base_key}|{seq}".encode()).hexdigest()


def _recompute_fingerprints(bind) -> int:
    """Rewrite every transaction's fingerprint; return the number updated."""
    rows = bind.execute(
        sa.select(
            _txns.c.id,
            _txns.c.import_id,
            _txns.c.account_id,
            _txns.c.date,
            _txns.c.amount,
            _txns.c.raw_description,
        ).order_by(_txns.c.import_id, _txns.c.id)
    ).all()

    counts: Counter[tuple] = Counter()
    updates: list[dict] = []
    for row in rows:
        amount = str(Decimal(row.amount).quantize(_CENTS))
        date_iso = row.date.isoformat()
        # Per-import occurrence sequence, mirroring compute_fingerprints,
        # which is called once per import file.
        key = (row.import_id, date_iso, amount, row.raw_description, row.account_id)
        seq = counts[key]
        counts[key] += 1
        fp = _fingerprint(date_iso, amount, row.raw_description, row.account_id, seq)
        updates.append({"row_id": row.id, "fp": fp})

    if updates:
        bind.execute(
            sa.text("UPDATE transactions SET fingerprint = :fp WHERE id = :row_id"),
            updates,
        )
    return len(updates)


def _remove_duplicates(bind) -> int:
    """Collapse each fingerprint to one row; return the number removed.

    Keeps the row carrying a user correction (else the lowest id) so manual
    fixes survive; deletes any correction on the removed rows explicitly.
    """
    dup_ids = [
        r[0]
        for r in bind.execute(
            sa.text(
                """
                SELECT id FROM (
                    SELECT t.id,
                           ROW_NUMBER() OVER (
                               PARTITION BY t.fingerprint
                               ORDER BY
                                   (SELECT COUNT(*) FROM transaction_corrections c
                                    WHERE c.transaction_id = t.id) DESC,
                                   t.id ASC
                           ) AS rn
                    FROM transactions t
                )
                WHERE rn > 1
                """
            )
        ).all()
    ]
    if not dup_ids:
        return 0

    id_rows = [{"row_id": i} for i in dup_ids]
    bind.execute(
        sa.text(
            "DELETE FROM transaction_corrections WHERE transaction_id = :row_id"
        ),
        id_rows,
    )
    bind.execute(
        sa.text("DELETE FROM transactions WHERE id = :row_id"),
        id_rows,
    )
    return len(dup_ids)


def upgrade() -> None:
    bind = op.get_bind()
    _backup_db(bind)
    updated = _recompute_fingerprints(bind)
    removed = _remove_duplicates(bind)
    print(
        f"recompute_transaction_fingerprints: recomputed {updated} fingerprints, "
        f"removed {removed} duplicate transactions"
    )


def downgrade() -> None:
    raise RuntimeError(
        "recompute_transaction_fingerprints is irreversible; restore "
        "<db>.pre-dedup-fix.bak to roll back."
    )
