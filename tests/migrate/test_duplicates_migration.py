"""Migration d5e6f7a8b9c0 (#82): transactions.external_id + transaction_duplicates.

Seeds a transaction with a correction, upgrades, and checks existing rows keep
their data with a NULL external_id; then downgrades and checks the column is
gone without the table rebuild cascading away the correction.
"""

import os
import sqlite3

from alembic import command
from alembic.config import Config

from fintrack.core.config import CATEGORIES_CONFIG  # noqa: F401 (anchors repo root)

_PREV = "c4d5e6f7a8b9"
_HEAD = "d5e6f7a8b9c0"
_ALEMBIC_INI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "alembic.ini"
)


def _alembic(db_path, fn, revision):
    os.environ["FINTRACK_DB"] = str(db_path)
    fn(Config(_ALEMBIC_INI), revision)


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_duplicates_migration_round_trip(tmp_path):
    db = tmp_path / "fintrack.db"
    _alembic(db, command.upgrade, _PREV)
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        INSERT INTO snapshots (id, name) VALUES (1, 's');
        INSERT INTO holdings (id, snapshot_id, group_key, type, name, sort_order)
            VALUES (1, 1, 'credit_card', 'credit_card', 'Card', 0);
        INSERT INTO imports (id, account_id, holding_group, filename, file_hash,
                             status)
            VALUES (1, 1, 'credit_card', 'a.qfx', 'h', 'confirmed');
        INSERT INTO transactions (id, import_id, account_id, date, amount,
                                  raw_description, normalized_merchant,
                                  fingerprint)
            VALUES (1, 1, 1, '2026-10-01', -5, 'COFFEE', 'COFFEE', 'fp');
        INSERT INTO transaction_corrections (transaction_id, notes)
            VALUES (1, 'keep me');
        """
    )
    conn.commit()
    conn.close()

    _alembic(db, command.upgrade, _HEAD)
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT raw_description, external_id FROM transactions"
    ).fetchall() == [("COFFEE", None)]
    assert "duplicate_of_id" in _columns(conn, "transaction_duplicates")
    conn.close()

    _alembic(db, command.downgrade, _PREV)
    conn = sqlite3.connect(db)
    assert "external_id" not in _columns(conn, "transactions")
    assert conn.execute("SELECT notes FROM transaction_corrections").fetchall() == [
        ("keep me",)
    ]
    conn.close()


def test_upgrade_after_app_startup_created_the_table(tmp_path):
    # The app/CLI run metadata.create_all() on startup: on a database still at
    # the previous revision that creates transaction_duplicates (not the
    # column). The upgrade must still succeed.
    from sqlalchemy import create_engine

    from fintrack.core.db import init_db

    db = tmp_path / "fintrack.db"
    _alembic(db, command.upgrade, _PREV)
    init_db(create_engine(f"sqlite:///{db}"))
    _alembic(db, command.upgrade, _HEAD)
    conn = sqlite3.connect(db)
    assert "external_id" in _columns(conn, "transactions")
    assert "duplicate_of_id" in _columns(conn, "transaction_duplicates")
    conn.close()
