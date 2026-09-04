"""Regression test for the fingerprint-recompute migration (c1d2e3f4a5b6).

Builds a database at the holdings-split head, seeds transactions with stale
fingerprints (as the split migration left them) plus bug-created cross-import
duplicates, upgrades, and asserts: fingerprints are recomputed to the value a
fresh import would produce (so dedup works again), duplicates are collapsed to
one row, legitimate in-file repeats survive, and a correction on a removed row
is preserved by keeping its transaction instead.
"""

import os
import sqlite3
from datetime import date
from decimal import Decimal

from alembic import command
from alembic.config import Config

from fintrack.core.config import CATEGORIES_CONFIG  # noqa: F401 (anchors repo root)
from fintrack.core.types import ParsedTransaction
from fintrack.ledger.importer.dedup import compute_fingerprints

_SPLIT_HEAD = "a7b8c9d0e1f2"
_HEAD = "f1e2d3c4b5a6"
_ALEMBIC_INI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "alembic.ini"
)

_ACCOUNT_ID = 5


def _upgrade(db_path, revision):
    os.environ["FINTRACK_DB"] = str(db_path)
    command.upgrade(Config(_ALEMBIC_INI), revision)


def _seed(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(
        f"""
        INSERT INTO snapshots (id, name) VALUES (1, 's');
        INSERT INTO holdings
            (id, snapshot_id, group_key, type, name, sort_order)
            VALUES ({_ACCOUNT_ID}, 1, 'cash', 'checking', 'Chk', 0);
        INSERT INTO cash_details (holding_id, group_key, snapshot_id, balance)
            VALUES ({_ACCOUNT_ID}, 'cash', 1, 1000);
        INSERT INTO imports
            (id, account_id, holding_group, filename, file_hash, status)
            VALUES (100, {_ACCOUNT_ID}, 'cash', 'jan.ofx', 'h1', 'confirmed'),
                   (101, {_ACCOUNT_ID}, 'cash', 'jan-again.ofx', 'h2', 'confirmed');

        -- A: same transaction in two imports (the bug's double-count). Stored
        --    fingerprints are stale (mismatched); recompute must make them agree.
        INSERT INTO transactions
            (id, import_id, account_id, date, amount, raw_description,
             normalized_merchant, fingerprint)
            VALUES
            (200, 100, {_ACCOUNT_ID}, '2026-01-01', -12.5, 'COFFEE', 'coffee', 'stale_a'),
            (201, 101, {_ACCOUNT_ID}, '2026-01-01', -12.5, 'COFFEE', 'coffee', 'stale_b');

        -- B: legitimate in-file repeat (two identical rows in one import).
        INSERT INTO transactions
            (id, import_id, account_id, date, amount, raw_description,
             normalized_merchant, fingerprint)
            VALUES
            (210, 100, {_ACCOUNT_ID}, '2026-01-02', -5.00, 'BUS', 'bus', 'stale_c'),
            (211, 100, {_ACCOUNT_ID}, '2026-01-02', -5.00, 'BUS', 'bus', 'stale_d');

        -- C: cross-import duplicate whose correction sits on the higher id.
        INSERT INTO transactions
            (id, import_id, account_id, date, amount, raw_description,
             normalized_merchant, fingerprint)
            VALUES
            (220, 100, {_ACCOUNT_ID}, '2026-01-03', -20.00, 'GAS', 'gas', 'stale_e'),
            (221, 101, {_ACCOUNT_ID}, '2026-01-03', -20.00, 'GAS', 'gas', 'stale_f');
        INSERT INTO transaction_corrections (id, transaction_id, category)
            VALUES (1, 221, 'Transport');
        """
    )
    conn.commit()
    conn.close()


def _expected_fp(day, amount, desc):
    txn = ParsedTransaction(
        date=date(2026, 1, day), amount=Decimal(amount), raw_description=desc
    )
    return compute_fingerprints([txn], account_id=_ACCOUNT_ID)[0]


def test_recompute_migration(tmp_path):
    db = tmp_path / "fintrack.db"
    _upgrade(db, _SPLIT_HEAD)
    _seed(db)
    _upgrade(db, _HEAD)

    conn = sqlite3.connect(db)
    rows = {
        r[0]: r[1]
        for r in conn.execute("SELECT id, fingerprint FROM transactions").fetchall()
    }
    corrections = {
        r[0]
        for r in conn.execute(
            "SELECT transaction_id FROM transaction_corrections"
        ).fetchall()
    }
    conn.close()

    # A: one COFFEE row survives (the lower id), with the canonical fingerprint
    #    a fresh import would compute -- proving re-import will now dedup.
    assert 200 in rows and 201 not in rows
    assert rows[200] == _expected_fp(1, "-12.50", "COFFEE")

    # B: both legitimate in-file repeats survive, with distinct fingerprints.
    assert 210 in rows and 211 in rows
    assert rows[210] != rows[211]

    # C: the corrected row (higher id) is the survivor, and its correction is kept.
    assert 221 in rows and 220 not in rows
    assert rows[221] == _expected_fp(3, "-20.00", "GAS")
    assert corrections == {221}
