"""Migration f2a3b4c5d6e7 (#101): the alert_dismissals table, up and down."""

import os
import sqlite3

import pytest
from alembic import command
from alembic.config import Config

_PREV = "e7f8a9b0c1d2"
_HEAD = "f2a3b4c5d6e7"
_ALEMBIC_INI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "alembic.ini"
)


def _alembic(db_path, fn, revision):
    os.environ["FINTRACK_DB"] = str(db_path)
    fn(Config(_ALEMBIC_INI), revision)


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}


def test_alert_dismissals_migration_round_trip(tmp_path):
    db = tmp_path / "fintrack.db"
    _alembic(db, command.upgrade, _PREV)
    _alembic(db, command.upgrade, _HEAD)
    conn = sqlite3.connect(db)
    assert "alert_dismissals" in _tables(conn)
    conn.execute("INSERT INTO snapshots (id, name) VALUES (1, 's')")
    conn.execute(
        "INSERT INTO alert_dismissals (snapshot_id, category, month)"
        " VALUES (1, 'Dining', '2026-11-01')"
    )
    # A row names a transaction or a category-month, never neither.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO alert_dismissals (snapshot_id) VALUES (1)")
    conn.commit()
    conn.close()

    _alembic(db, command.downgrade, _PREV)
    conn = sqlite3.connect(db)
    assert "alert_dismissals" not in _tables(conn)
    conn.close()
