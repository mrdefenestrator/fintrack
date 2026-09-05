"""Data-copy test for the holdings-split Alembic migration (a7b8c9d0e1f2).

Builds a database at the pre-split revision, seeds old-schema rows through raw
SQL, upgrades to head, and asserts the supertype/subtype tables carry the data
faithfully (group routing, signed loan balances, remapped refs, dropped
`available`, importable-name uniqueness), plus that preflight aborts on the
unsupported shapes without touching the database.
"""

import os
import sqlite3
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config

from fintrack.core.config import CATEGORIES_CONFIG  # noqa: F401 (anchors repo root)

_PRE_SPLIT = "f4a5b6c7d8e9"
_HEAD = "a7b8c9d0e1f2"
_ALEMBIC_INI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "alembic.ini"
)


def _upgrade(db_path, revision):
    os.environ["FINTRACK_DB"] = str(db_path)
    command.upgrade(Config(_ALEMBIC_INI), revision)


def _seed_old_schema(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        INSERT INTO snapshots (id, name) VALUES (1, 's'), (2, 't');
        -- cash / credit (pays from cash 1) / account-loan (pays from cash 1)
        INSERT INTO accounts
            (id, snapshot_id, name, institution, account_type, balance,
             minimum_balance, sort_order)
            VALUES (1, 1, 'Chk', 'B', 'checking', 1000, 200, 0);
        INSERT INTO accounts
            (id, snapshot_id, name, institution, account_type, balance,
             credit_limit, available, rewards_balance, statement_balance,
             statement_due_day_of_month, payment_account_ref, sort_order)
            VALUES (2, 1, 'Visa', 'B', 'credit_card', -300, 1000, 700, 10, 300,
                    15, 1, 1);
        INSERT INTO accounts
            (id, snapshot_id, name, institution, account_type, balance,
             statement_due_day_of_month, payment_account_ref, sort_order)
            VALUES (3, 1, 'CarLoanAcct', 'B', 'loan', -5000, 5, 1, 2);
        -- asset / secured debt (quantity fold, signed) / unclassified symbol asset
        INSERT INTO asset_entries
            (id, snapshot_id, kind, type, unit, name, value, quantity, sort_order)
            VALUES (10, 1, 'asset', 'real_estate', 'USD', 'Home', 500000, 1, 0);
        INSERT INTO asset_entries
            (id, snapshot_id, kind, type, name, balance, quantity, asset_ref,
             interest_rate, original_principal, term_months, sort_order)
            VALUES (11, 1, 'debt', 'loan', 'Mortgage', 300000, 1, 10, 0.05,
                    350000, 360, 1);
        INSERT INTO asset_entries
            (id, snapshot_id, kind, type, unit, name, value, quantity, sort_order)
            VALUES (12, 1, 'asset', NULL, 'BTC', 'Wallet', 60000, 0.5, 2);
        -- ledger rows hanging off the credit card
        INSERT INTO imports (id, account_id, filename, file_hash, status,
                             ledger_balance)
            VALUES (100, 2, 'f.ofx', 'h', 'confirmed', -300);
        INSERT INTO transactions
            (id, import_id, account_id, date, amount, raw_description,
             normalized_merchant, fingerprint)
            VALUES (200, 100, 2, '2026-01-01', -50, 'X', 'x', 'fp1');
        INSERT INTO balance_history (id, account_id, as_of, balance, source)
            VALUES (300, 2, '2026-01-01', -300, 'statement');
        INSERT INTO budget_entries
            (id, snapshot_id, kind, description, amount, recurrence,
             auto_account_ref, sort_order)
            VALUES (400, 1, 'expense', 'Rent', 1000, 'monthly', 1, 0);
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture
def migrated_db(tmp_path):
    db = tmp_path / "split.db"
    _upgrade(db, _PRE_SPLIT)
    _seed_old_schema(db)
    _upgrade(db, _HEAD)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _rows(conn, sql):
    return [dict(r) for r in conn.execute(sql).fetchall()]


def test_holdings_spine_group_routing(migrated_db):
    holdings = {
        r["name"]: r
        for r in _rows(
            migrated_db, "SELECT name, group_key, type, sort_order FROM holdings"
        )
    }
    assert holdings["Chk"]["group_key"] == "cash"
    assert holdings["Visa"]["group_key"] == "credit_card"
    # account-loan and debt-loan both land in the loan band
    assert holdings["CarLoanAcct"]["group_key"] == "loan"
    assert holdings["Mortgage"]["group_key"] == "loan"
    assert holdings["Home"]["group_key"] == "asset"
    assert holdings["Wallet"]["group_key"] == "asset"
    assert holdings["Wallet"]["type"] is None  # unclassified survives
    # sort_order re-indexed per (snapshot, group); account-loan before debt-loan
    assert holdings["CarLoanAcct"]["sort_order"] == 0
    assert holdings["Mortgage"]["sort_order"] == 1


def test_detail_tables_carry_columns(migrated_db):
    cash = _rows(migrated_db, "SELECT * FROM cash_details")[0]
    assert cash["balance"] == 1000 and cash["minimum_balance"] == 200

    cc = _rows(migrated_db, "SELECT * FROM credit_card_details")[0]
    assert cc["balance"] == -300 and cc["credit_limit"] == 1000
    assert cc["statement_due_day_of_month"] == 15
    # available is not a column any more (D4)
    assert "available" not in cc

    # debt balance stored signed (negative = owed), quantity folded in
    loans = {r["balance"]: r for r in _rows(migrated_db, "SELECT * FROM loan_details")}
    assert -300000 in loans and loans[-300000]["original_principal"] == 350000
    assert -5000 in loans  # the account-loan, balance copied as-is


def test_references_remapped(migrated_db):
    name_by_id = {
        r["id"]: r["name"] for r in _rows(migrated_db, "SELECT id, name FROM holdings")
    }
    cc = _rows(migrated_db, "SELECT * FROM credit_card_details")[0]
    assert name_by_id[cc["payment_account_ref"]] == "Chk"
    mortgage = next(
        r
        for r in _rows(migrated_db, "SELECT * FROM loan_details")
        if r["balance"] == -300000
    )
    assert name_by_id[mortgage["secured_asset_ref"]] == "Home"
    budget = _rows(migrated_db, "SELECT * FROM budget_entries")[0]
    assert name_by_id[budget["auto_account_ref"]] == "Chk"


def test_ledger_rows_follow_holding(migrated_db):
    visa_id = migrated_db.execute(
        "SELECT id FROM holdings WHERE name='Visa'"
    ).fetchone()[0]
    imp = _rows(migrated_db, "SELECT * FROM imports")[0]
    assert imp["account_id"] == visa_id and imp["holding_group"] == "credit_card"
    assert (
        _rows(migrated_db, "SELECT account_id FROM transactions")[0]["account_id"]
        == visa_id
    )
    assert (
        _rows(migrated_db, "SELECT account_id FROM balance_history")[0]["account_id"]
        == visa_id
    )


def test_integrity_and_old_tables_gone(migrated_db):
    assert migrated_db.execute("PRAGMA foreign_key_check").fetchall() == []
    leftover = migrated_db.execute(
        "SELECT name FROM sqlite_master WHERE name IN "
        "('accounts','asset_entries','map_accounts','map_assets')"
    ).fetchall()
    assert leftover == []


def test_asset_unit_and_quantity_preserved(migrated_db):
    wallet = next(
        r
        for r in _rows(migrated_db, "SELECT * FROM asset_details")
        if r["unit"] == "BTC"
    )
    assert wallet["quantity"] == 0.5 and wallet["value"] == 60000


def test_preflight_aborts_on_symbol_debt(tmp_path):
    db = tmp_path / "abort.db"
    _upgrade(db, _PRE_SPLIT)
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        INSERT INTO snapshots (id, name) VALUES (1, 's');
        INSERT INTO asset_entries
            (id, snapshot_id, kind, type, unit, name, balance, sort_order)
            VALUES (1, 1, 'debt', 'loan', 'BTC', 'X', 1, 0);
        """
    )
    conn.commit()
    conn.close()
    with pytest.raises(Exception, match="symbol-denominated debts"):
        _upgrade(db, _HEAD)
    # DB untouched: old table present, new table not created.
    conn = sqlite3.connect(db)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}
    conn.close()
    assert "asset_entries" in names and "holdings" not in names


def test_preflight_aborts_on_duplicate_importable_name(tmp_path):
    db = tmp_path / "dup.db"
    _upgrade(db, _PRE_SPLIT)
    conn = sqlite3.connect(db)
    # An account and a debt that collide on (snapshot, institution, name): the
    # debt newly joins the importable-name rule, so this must abort.
    conn.executescript(
        """
        INSERT INTO snapshots (id, name) VALUES (1, 's');
        INSERT INTO accounts
            (id, snapshot_id, name, institution, account_type, sort_order)
            VALUES (1, 1, 'Loan', 'B', 'checking', 0);
        INSERT INTO asset_entries
            (id, snapshot_id, kind, type, institution, name, balance, sort_order)
            VALUES (2, 1, 'debt', 'loan', 'B', 'Loan', 100, 0);
        """
    )
    conn.commit()
    conn.close()
    with pytest.raises(Exception, match="uniquely named"):
        _upgrade(db, _HEAD)


def test_migrated_schema_matches_create_all(tmp_path):
    from sqlalchemy import create_engine, inspect

    from fintrack.core.db import init_db

    mig = tmp_path / "mig.db"
    _upgrade(mig, _HEAD)  # fresh empty DB -> full chain
    created = tmp_path / "created.db"
    init_db(create_engine(f"sqlite:///{created}"))

    def _tables(path):
        return set(inspect(create_engine(f"sqlite:///{path}")).get_table_names()) - {
            "alembic_version"
        }

    assert _tables(mig) == _tables(created)


def test_no_available_value_lost_silently(migrated_db):
    # The credit card's balance is canonical; available (700) was dropped and
    # is recomputed as credit_limit + balance = 1000 + (-300) = 700 downstream.
    cc = _rows(migrated_db, "SELECT balance, credit_limit FROM credit_card_details")[0]
    assert Decimal(str(cc["credit_limit"])) + Decimal(str(cc["balance"])) == Decimal(
        700
    )
