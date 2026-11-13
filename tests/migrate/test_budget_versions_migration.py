"""Migration a8b9c0d1e2f3 (#136): budget entries' start/end months and the
budget_entry_versions table.

Existing entries keep NULL bounds (planned in every month, as before) and
their transaction links; downgrade refuses while any budget history exists,
and doesn't rebuild budget_entries (which would SET NULL the links).
"""

import os
import sqlite3

import pytest
from alembic import command
from alembic.config import Config

_PREV = "e6f7a8b9c0d1"
_HEAD = "a8b9c0d1e2f3"
_ALEMBIC_INI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "alembic.ini"
)


def _alembic(db_path, fn, revision):
    os.environ["FINTRACK_DB"] = str(db_path)
    fn(Config(_ALEMBIC_INI), revision)


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}


def _seed(db):
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        INSERT INTO snapshots (id, name) VALUES (1, 's');
        INSERT INTO holdings (id, snapshot_id, group_key, type, name, sort_order)
            VALUES (1, 1, 'cash', 'checking', 'Chk', 0);
        INSERT INTO imports (id, account_id, holding_group, filename, file_hash,
                             status)
            VALUES (1, 1, 'cash', 'a.qfx', 'h', 'confirmed');
        INSERT INTO transactions (id, import_id, account_id, date, amount,
                                  raw_description, normalized_merchant,
                                  fingerprint)
            VALUES (1, 1, 1, '2026-10-01', -5, 'X', 'X', 'fp');
        INSERT INTO budget_entries (id, snapshot_id, kind, description, amount,
                                    recurrence, sort_order)
            VALUES (1, 1, 'expense', 'e', 5, 'monthly', 0);
        INSERT INTO transaction_corrections (transaction_id, budget_entry_ref)
            VALUES (1, 1);
        """
    )
    conn.commit()
    conn.close()


def _link(conn):
    return conn.execute(
        "SELECT budget_entry_ref FROM transaction_corrections"
    ).fetchall()


def test_budget_versions_migration_round_trip(tmp_path):
    db = tmp_path / "fintrack.db"
    _alembic(db, command.upgrade, _PREV)
    _seed(db)

    _alembic(db, command.upgrade, _HEAD)
    conn = sqlite3.connect(db)
    assert {"start_month", "end_month"} <= _columns(conn, "budget_entries")
    assert "budget_entry_versions" in _tables(conn)
    assert conn.execute(
        "SELECT start_month, end_month FROM budget_entries"
    ).fetchall() == [(None, None)]
    assert _link(conn) == [(1,)]

    # Budget history blocks the downgrade (it would be lost).
    conn.execute(
        "INSERT INTO budget_entry_versions (entry_id, effective_from, amount,"
        " recurrence) VALUES (1, '2026-09-01', 6, 'monthly')"
    )
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="budget history"):
        _alembic(db, command.downgrade, _PREV)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM budget_entry_versions")
    conn.commit()
    conn.close()

    _alembic(db, command.downgrade, _PREV)
    conn = sqlite3.connect(db)
    assert "budget_entry_versions" not in _tables(conn)
    assert "start_month" not in _columns(conn, "budget_entries")
    assert _link(conn) == [(1,)]
    conn.close()


def test_upgrade_after_create_all(tmp_path):
    """The app's create_all() may already have made the new table (but not
    the new columns) on an old database; the upgrade fills in the rest."""
    db = tmp_path / "fintrack.db"
    _alembic(db, command.upgrade, _PREV)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE budget_entry_versions (id INTEGER PRIMARY KEY,"
        " entry_id INTEGER NOT NULL, effective_from DATE NOT NULL,"
        " amount NUMERIC NOT NULL, recurrence VARCHAR NOT NULL, date DATE,"
        " day_of_month INTEGER, month INTEGER, day_of_year INTEGER,"
        " continuous BOOLEAN)"
    )
    conn.commit()
    conn.close()
    _alembic(db, command.upgrade, _HEAD)
    conn = sqlite3.connect(db)
    assert {"start_month", "end_month"} <= _columns(conn, "budget_entries")
    conn.close()
