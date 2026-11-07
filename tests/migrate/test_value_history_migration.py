"""Migration e7f8a9b0c1d2 (#107): 'price' + 'baseline' balance_history sources,
and a baseline point for every loan and asset with no history yet."""

import os
import sqlite3

import pytest
from alembic import command
from alembic.config import Config

from fintrack.core.config import CATEGORIES_CONFIG  # noqa: F401 (anchors repo root)

_PREV = "d5e6f7a8b9c0"
_HEAD = "e7f8a9b0c1d2"
_ALEMBIC_INI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "alembic.ini"
)


def _alembic(db_path, fn, revision):
    os.environ["FINTRACK_DB"] = str(db_path)
    fn(Config(_ALEMBIC_INI), revision)


def test_value_history_migration_round_trip(tmp_path):
    db = tmp_path / "fintrack.db"
    _alembic(db, command.upgrade, _PREV)
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        INSERT INTO snapshots (id, name) VALUES (1, 's');
        INSERT INTO holdings (id, snapshot_id, group_key, type, name, sort_order,
                              as_of_date)
            VALUES (1, 1, 'loan', 'loan', 'Car', 0, '2026-09-01'),
                   (2, 1, 'asset', 'real_estate', 'House', 0, NULL),
                   (3, 1, 'asset', 'brokerage', 'Coins', 1, '2026-10-01'),
                   (4, 1, 'loan', 'loan', 'Old', 1, '2026-01-01');
        INSERT INTO loan_details (holding_id, group_key, snapshot_id, balance)
            VALUES (1, 'loan', 1, -12000), (4, 'loan', 1, -500);
        INSERT INTO asset_details (holding_id, group_key, snapshot_id, unit,
                                   quantity, value)
            VALUES (2, 'asset', 1, 'USD', NULL, 300000),
                   (3, 'asset', 1, 'BTC', 2, 1);
        INSERT INTO price_cache (unit, price_usd, fetched_at)
            VALUES ('BTC', 50000, '2026-10-01 00:00:00');
        INSERT INTO balance_history (account_id, as_of, balance, source)
            VALUES (4, '2026-01-01', -500, 'manual');
        """
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO balance_history (account_id, as_of, balance, source) "
            "VALUES (1, '2026-01-01', 0, 'price')"
        )
    conn.close()

    _alembic(db, command.upgrade, _HEAD)
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT account_id, as_of, balance FROM balance_history "
        "WHERE source = 'baseline' ORDER BY account_id"
    ).fetchall()
    assert [(r[0], r[2]) for r in rows] == [(1, -12000), (2, 300000), (3, 100000)]
    assert rows[0][1] == "2026-09-01"
    assert rows[2][1] == "2026-10-01"
    # House had no As Of: dated today, and its As Of now caches that point.
    house_as_of = conn.execute("SELECT as_of_date FROM holdings WHERE id = 2")
    assert house_as_of.fetchone()[0] == rows[1][1]
    # The loan that already had history got no baseline.
    assert conn.execute(
        "SELECT count(*) FROM balance_history WHERE account_id = 4"
    ).fetchone() == (1,)
    conn.execute(
        "INSERT INTO balance_history (account_id, as_of, balance, source) "
        "VALUES (3, '2026-11-01', 1, 'price')"
    )
    conn.commit()
    conn.close()

    _alembic(db, command.downgrade, _PREV)
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT account_id, source FROM balance_history"
    ).fetchall() == [(4, "manual")]
    conn.close()
