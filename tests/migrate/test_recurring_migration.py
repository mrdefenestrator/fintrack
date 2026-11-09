"""Migration e6f7a8b9c0d1 (#100): weekly budget recurrence and the
recurring_dismissals table.

Rebuilding budget_entries to widen its recurrence CHECK must not set existing
transactions' budget links to NULL or cascade away link-suggestion
dismissals; downgrade refuses while weekly entries exist.
"""

import os
import sqlite3

import pytest
from alembic import command
from alembic.config import Config

_PREV = "f2a3b4c5d6e7"
_HEAD = "e6f7a8b9c0d1"
_ALEMBIC_INI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "alembic.ini"
)


def _alembic(db_path, fn, revision):
    os.environ["FINTRACK_DB"] = str(db_path)
    fn(Config(_ALEMBIC_INI), revision)


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
        INSERT INTO link_suggestion_dismissals (transaction_id, budget_entry_ref)
            VALUES (1, 1);
        """
    )
    conn.commit()
    conn.close()


def _weekly(conn):
    conn.execute(
        "INSERT INTO budget_entries (snapshot_id, kind, description, amount,"
        " recurrence, sort_order) VALUES (1, 'expense', 'w', 6, 'weekly', 1)"
    )


def test_recurring_migration_round_trip(tmp_path):
    db = tmp_path / "fintrack.db"
    _alembic(db, command.upgrade, _PREV)
    _seed(db)
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        _weekly(conn)
    conn.close()

    _alembic(db, command.upgrade, _HEAD)
    conn = sqlite3.connect(db)
    assert "recurring_dismissals" in _tables(conn)
    # The rebuild kept the link and the dismissal.
    assert conn.execute(
        "SELECT budget_entry_ref FROM transaction_corrections"
    ).fetchall() == [(1,)]
    assert conn.execute(
        "SELECT count(*) FROM link_suggestion_dismissals"
    ).fetchone() == (1,)
    _weekly(conn)
    conn.commit()
    conn.close()

    # Weekly entries block the downgrade (the old CHECK can't hold them).
    with pytest.raises(RuntimeError, match="weekly"):
        _alembic(db, command.downgrade, _PREV)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM budget_entries WHERE recurrence = 'weekly'")
    conn.commit()
    conn.close()

    _alembic(db, command.downgrade, _PREV)
    conn = sqlite3.connect(db)
    assert "recurring_dismissals" not in _tables(conn)
    assert conn.execute(
        "SELECT budget_entry_ref FROM transaction_corrections"
    ).fetchall() == [(1,)]
    with pytest.raises(sqlite3.IntegrityError):
        _weekly(conn)
    conn.close()
