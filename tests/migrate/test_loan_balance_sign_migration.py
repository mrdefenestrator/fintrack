"""Regression test for the loan-balance sign migration (c4d5e6f7a8b9, #79).

Seeds a loan whose OFX import stored the principal owed as a positive
statement balance, plus a credit card with a legitimately positive (credit)
statement balance and a manual loan point, upgrades, and asserts only the
loan's statement data flips and its cached balance re-syncs.
"""

import os
import sqlite3
from decimal import Decimal

from alembic import command
from alembic.config import Config

from fintrack.core.config import CATEGORIES_CONFIG  # noqa: F401 (anchors repo root)

_PREV = "b3c4d5e6f7a8"
_HEAD = "c4d5e6f7a8b9"
_ALEMBIC_INI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "alembic.ini"
)


def _upgrade(db_path, revision):
    os.environ["FINTRACK_DB"] = str(db_path)
    command.upgrade(Config(_ALEMBIC_INI), revision)


def _seed(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        INSERT INTO snapshots (id, name) VALUES (1, 's');
        INSERT INTO holdings (id, snapshot_id, group_key, type, name, sort_order)
            VALUES (1, 1, 'loan', 'loan', 'Mortgage', 0),
                   (2, 1, 'credit_card', 'credit_card', 'Visa', 0),
                   (3, 1, 'loan', 'loan', 'Car', 1);
        INSERT INTO loan_details (holding_id, group_key, snapshot_id, balance)
            VALUES (1, 'loan', 1, 250000), (3, 'loan', 1, -9000);
        INSERT INTO credit_card_details
            (holding_id, group_key, snapshot_id, balance)
            VALUES (2, 'credit_card', 1, 40);
        INSERT INTO imports
            (id, account_id, holding_group, filename, file_hash, status,
             ledger_balance)
            VALUES (10, 1, 'loan', 'jan.qfx', 'h1', 'confirmed', 251000),
                   (11, 1, 'loan', 'feb.qfx', 'h2', 'confirmed', 250000),
                   (12, 2, 'credit_card', 'cc.qfx', 'h3', 'confirmed', 40);
        INSERT INTO balance_history
            (id, account_id, as_of, balance, source, import_id)
            VALUES (1, 1, '2026-01-31', 251000, 'statement', 10),
                   (2, 1, '2026-02-28', 250000, 'statement', 11),
                   (3, 2, '2026-02-28', 40, 'statement', 12),
                   (4, 3, '2026-02-28', -9000, 'manual', NULL);
        """
    )
    conn.commit()
    conn.close()


def test_loan_balance_sign_migration(tmp_path):
    db = tmp_path / "fintrack.db"
    _upgrade(db, _PREV)
    _seed(db)
    _upgrade(db, _HEAD)

    conn = sqlite3.connect(db)

    def scalar_map(sql):
        return {r[0]: Decimal(str(r[1])) for r in conn.execute(sql).fetchall()}

    imports = scalar_map("SELECT id, ledger_balance FROM imports")
    history = scalar_map("SELECT id, balance FROM balance_history")
    loans = scalar_map("SELECT holding_id, balance FROM loan_details")
    cards = scalar_map("SELECT holding_id, balance FROM credit_card_details")
    conn.close()

    assert imports == {10: -251000, 11: -250000, 12: 40}
    assert history == {1: -251000, 2: -250000, 3: 40, 4: -9000}
    # Cached loan balance re-synced to its latest (Feb) point.
    assert loans == {1: -250000, 3: -9000}
    assert cards == {2: 40}
