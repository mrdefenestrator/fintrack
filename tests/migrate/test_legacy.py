"""migrate-legacy tests against fixture databases built with the LEGACY schemas.

The fixtures replicate the final pre-merge schemas of spending.db (Alembic
d7921ed1b928) and finances.db (bc34fd41671a) via raw DDL, so these tests keep
working no matter how the current models evolve.
"""

import sqlite3
from datetime import date
from decimal import Decimal

import pytest
import yaml
from sqlalchemy import select

from fintrack.core import models
from fintrack.core.db import get_engine
from fintrack.ledger.importer.dedup import compute_fingerprints
from fintrack.migrate.legacy import (
    MigrationError,
    apply_migration,
    load_mapping,
    render_mapping_template,
)

_SPENDING_DDL = """
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE,
    institution VARCHAR NOT NULL, account_type VARCHAR NOT NULL,
    created_at DATETIME);
CREATE TABLE imports (
    id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL,
    filename VARCHAR NOT NULL, file_hash VARCHAR NOT NULL,
    imported_at DATETIME, status VARCHAR NOT NULL,
    ledger_balance NUMERIC(12, 2), ledger_balance_date DATE,
    available_balance NUMERIC(12, 2), available_balance_date DATE,
    beginning_balance NUMERIC(12, 2));
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY, import_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL, date DATE NOT NULL,
    amount NUMERIC(10, 2) NOT NULL, raw_description VARCHAR NOT NULL,
    normalized_merchant VARCHAR NOT NULL, fingerprint VARCHAR NOT NULL,
    created_at DATETIME);
CREATE TABLE merchant_cache (
    id INTEGER PRIMARY KEY, merchant_name VARCHAR NOT NULL UNIQUE,
    category VARCHAR NOT NULL, source VARCHAR NOT NULL,
    created_at DATETIME, updated_at DATETIME);
CREATE TABLE transaction_corrections (
    id INTEGER PRIMARY KEY, transaction_id INTEGER NOT NULL UNIQUE,
    category VARCHAR, merchant_name VARCHAR, notes VARCHAR,
    created_at DATETIME);
CREATE TABLE categories (
    id INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL);
"""

_FINANCES_DDL = """
CREATE TABLE fin_snapshots (
    id INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE, created_at DATETIME);
CREATE TABLE fin_accounts (
    id INTEGER PRIMARY KEY, snapshot_id INTEGER NOT NULL,
    name VARCHAR NOT NULL, type VARCHAR NOT NULL,
    balance NUMERIC(12, 2), "limit" NUMERIC(12, 2), available NUMERIC(12, 2),
    rewards_balance NUMERIC(12, 2), statement_balance NUMERIC(12, 2),
    statement_due_day_of_month INTEGER, payment_account_ref INTEGER,
    as_of_date VARCHAR, minimum_balance NUMERIC(12, 2), institution VARCHAR,
    partial_account_number VARCHAR, sort_order INTEGER NOT NULL);
CREATE TABLE fin_budget_entries (
    id INTEGER PRIMARY KEY, snapshot_id INTEGER NOT NULL,
    kind VARCHAR NOT NULL, description VARCHAR NOT NULL,
    amount NUMERIC(12, 2) NOT NULL, recurrence VARCHAR NOT NULL,
    type VARCHAR, date VARCHAR, day_of_month INTEGER, month INTEGER,
    day_of_year INTEGER, continuous BOOLEAN, auto_account_ref INTEGER,
    sort_order INTEGER NOT NULL);
CREATE TABLE fin_asset_entries (
    id INTEGER PRIMARY KEY, snapshot_id INTEGER NOT NULL,
    kind VARCHAR NOT NULL, name VARCHAR NOT NULL, institution VARCHAR,
    value NUMERIC(14, 2), source VARCHAR, quantity NUMERIC(12, 4),
    balance NUMERIC(14, 2), asset_ref INTEGER, interest_rate NUMERIC(8, 6),
    next_due_date VARCHAR, as_of_date VARCHAR, sort_order INTEGER NOT NULL);
"""


@pytest.fixture
def legacy_spending_db(tmp_path):
    path = tmp_path / "legacy_spending.db"
    db = sqlite3.connect(path)
    db.executescript(_SPENDING_DDL)
    db.execute(
        "INSERT INTO accounts VALUES (1, 'Chase Checking', 'Chase', 'checking',"
        " '2026-01-01 10:00:00')"
    )
    db.execute(
        "INSERT INTO accounts VALUES (2, 'Venmo', 'Venmo', 'checking',"
        " '2026-01-02 10:00:00')"
    )
    db.execute(
        "INSERT INTO imports VALUES (1, 1, 'jan.ofx', 'hash1',"
        " '2026-02-02 09:00:00', 'confirmed', 987.65, '2026-02-01',"
        " 900.00, '2026-02-01', NULL)"
    )
    db.execute(
        "INSERT INTO imports VALUES (2, 2, 'venmo.csv', 'hash2',"
        " '2026-02-03 09:00:00', 'staging', NULL, NULL, NULL, NULL, NULL)"
    )
    # Fingerprints exactly as the importer would have computed them, with the
    # LEGACY account ids — includes a same-day duplicate pair (sequence 0/1).
    txns = [
        {
            "date": date(2026, 1, 5),
            "amount": Decimal("-10.00"),
            "raw_description": "COFFEE",
        },
        {
            "date": date(2026, 1, 6),
            "amount": Decimal("-5.25"),
            "raw_description": "BAGEL",
        },
        {
            "date": date(2026, 1, 6),
            "amount": Decimal("-5.25"),
            "raw_description": "BAGEL",
        },
    ]
    fps = compute_fingerprints(txns, 1)
    for i, (t, fp) in enumerate(zip(txns, fps), start=1):
        db.execute(
            "INSERT INTO transactions VALUES (?, 1, 1, ?, ?, ?, ?, ?,"
            " '2026-02-02 09:00:00')",
            (
                i,
                t["date"].isoformat(),
                float(t["amount"]),
                t["raw_description"],
                t["raw_description"].title(),
                fp,
            ),
        )
    db.execute(
        "INSERT INTO merchant_cache VALUES (1, 'COFFEE', 'Dining', 'api',"
        " '2026-02-02 09:00:00', '2026-02-02 09:00:00')"
    )
    db.execute(
        "INSERT INTO transaction_corrections VALUES (1, 2, 'Groceries', NULL,"
        " 'fixed by hand', '2026-02-02 10:00:00')"
    )
    db.execute("INSERT INTO categories VALUES (1, 'Groceries', 1)")
    db.execute("INSERT INTO categories VALUES (2, 'Custom Legacy Cat', 2)")
    db.commit()
    db.close()
    return path


@pytest.fixture
def legacy_finances_db(tmp_path):
    path = tmp_path / "legacy_finances.db"
    db = sqlite3.connect(path)
    db.executescript(_FINANCES_DDL)
    db.execute("INSERT INTO fin_snapshots VALUES (1, 'mike', '2026-01-01 08:00:00')")
    db.execute("INSERT INTO fin_snapshots VALUES (2, 'dechen', '2026-01-01 08:00:00')")
    db.execute(
        "INSERT INTO fin_accounts VALUES (10, 1, 'Chase Checking', 'checking',"
        " 1000.00, NULL, NULL, NULL, NULL, NULL, NULL, '2026-01-15', 300.00,"
        " 'Chase', '1234', 1)"
    )
    db.execute(
        "INSERT INTO fin_accounts VALUES (11, 1, 'Chase Visa', 'credit_card',"
        " NULL, 5000.00, 4500.00, 25.00, -300.00, 15, 10, NULL, NULL,"
        " 'Chase', '5678', 2)"
    )
    db.execute(
        "INSERT INTO fin_accounts VALUES (12, 2, 'D Savings', 'savings',"
        " 2500.00, NULL, NULL, NULL, NULL, NULL, NULL, 'garbage-date', NULL,"
        " 'Ally', NULL, 1)"
    )
    db.execute(
        "INSERT INTO fin_budget_entries VALUES (20, 1, 'income', 'Salary',"
        " 5000.00, 'monthly', 'salary', NULL, 15, NULL, NULL, 0, 10, 1)"
    )
    db.execute(
        "INSERT INTO fin_asset_entries VALUES (30, 1, 'asset', 'House', NULL,"
        " 500000.00, 'zillow', NULL, NULL, NULL, NULL, NULL, '2026-01-10', 1)"
    )
    db.execute(
        "INSERT INTO fin_asset_entries VALUES (31, 1, 'debt', 'Mortgage',"
        " 'BankCo', NULL, NULL, NULL, 400000.00, 30, 0.05, 'not-a-date',"
        " NULL, 2)"
    )
    db.commit()
    db.close()
    return path


@pytest.fixture
def mapping_all(tmp_path):
    """Chase Checking merges into mike's finances account; Venmo is created in mike."""
    path = tmp_path / "mapping.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "spending_accounts": [
                    {
                        "name": "Chase Checking",
                        "merge_into": {"snapshot": "mike", "name": "Chase Checking"},
                    },
                    {"name": "Venmo", "snapshot": "mike"},
                ]
            }
        )
    )
    return path


def test_template_automatches_and_is_loadable(
    tmp_path, legacy_spending_db, legacy_finances_db
):
    text = render_mapping_template(legacy_spending_db, legacy_finances_db)
    assert "auto-matched" in text
    path = tmp_path / "template.yaml"
    path.write_text(text)
    mapping = load_mapping(path)
    assert mapping["Chase Checking"]["merge_into"] == {
        "snapshot": "mike",
        "name": "Chase Checking",
    }
    # Venmo has no finances counterpart -> assigned to the default snapshot
    assert mapping["Venmo"]["snapshot"] == "mike"


def test_apply_full_migration(
    tmp_path, legacy_spending_db, legacy_finances_db, mapping_all
):
    target = tmp_path / "fintrack.db"
    report = apply_migration(
        legacy_spending_db,
        legacy_finances_db,
        load_mapping(mapping_all),
        target,
    )
    assert "fingerprint self-check: OK" in report
    assert "unparseable date" in report  # garbage-date + not-a-date warned

    engine = get_engine(target)
    with engine.connect() as conn:
        # Accounts: 3 finances + 1 created (Venmo); Chase Checking merged
        accounts = conn.execute(select(models.accounts)).mappings().all()
        assert len(accounts) == 4
        by_name = {a["name"]: a for a in accounts}

        # partial_account_number is a dropped column; its value is folded
        # into the stored name instead ("Chase Checking [1234]").
        checking = by_name["Chase Checking [1234]"]
        visa = by_name["Chase Visa [5678]"]
        venmo = by_name["Venmo"]
        # merge kept finances metadata, statement balance re-synced from history
        assert checking["balance"] == Decimal("987.65")
        assert checking["as_of_date"] == date(2026, 2, 1)
        # CC canonical balance derived from available - limit
        assert visa["balance"] == Decimal("-500.00")
        # payment_account_ref remapped to the new checking id
        assert visa["payment_account_ref"] == checking["id"]
        # unmatched spending account created in mike with appended sort_order
        assert venmo["snapshot_id"] == checking["snapshot_id"]
        assert venmo["sort_order"] == 3

        # Bad as_of_date stored as null, balance still migrated
        assert by_name["D Savings"]["as_of_date"] == date.today()  # from history resync
        assert by_name["D Savings"]["balance"] == Decimal("2500.00")

        # Transactions: fingerprints recomputed with the NEW account id
        txns = (
            conn.execute(select(models.transactions).order_by(models.transactions.c.id))
            .mappings()
            .all()
        )
        assert len(txns) == 3
        expected = compute_fingerprints([dict(t) for t in txns], checking["id"])
        assert [t["fingerprint"] for t in txns] == expected
        assert txns[1]["fingerprint"] != txns[2]["fingerprint"]  # sequence pair

        # Correction remapped onto the migrated transaction
        corr = conn.execute(select(models.transaction_corrections)).mappings().one()
        assert corr["transaction_id"] == txns[1]["id"]

        # Budget/auto ref + asset/debt ref remapped
        budget = conn.execute(select(models.budget_entries)).mappings().one()
        assert budget["auto_account_ref"] == checking["id"]
        assets = {
            a["name"]: a
            for a in conn.execute(select(models.asset_entries)).mappings().all()
        }
        assert assets["Mortgage"]["asset_ref"] == assets["House"]["id"]
        assert assets["Mortgage"]["statement_due_day_of_month"] is None

        # balance_history: 1 statement row + 3 migration rows
        history = conn.execute(select(models.balance_history)).mappings().all()
        sources = sorted(h["source"] for h in history)
        assert sources == ["migration", "migration", "migration", "statement"]
        stmt_row = next(h for h in history if h["source"] == "statement")
        assert stmt_row["account_id"] == checking["id"]
        assert stmt_row["balance"] == Decimal("987.65")
        assert stmt_row["available"] == Decimal("900.00")

        # Categories: seed config plus the legacy-only category
        names = {r[0] for r in conn.execute(select(models.categories.c.name)).all()}
        assert "Custom Legacy Cat" in names
        assert "Groceries" in names
        assert len(names) > 3  # seeded list came in too

        # merchant cache copied
        cache = conn.execute(select(models.merchant_cache)).mappings().one()
        assert cache["merchant_name"] == "COFFEE"


def test_dry_run_rolls_back(
    tmp_path, legacy_spending_db, legacy_finances_db, mapping_all
):
    target = tmp_path / "fintrack.db"
    report = apply_migration(
        legacy_spending_db,
        legacy_finances_db,
        load_mapping(mapping_all),
        target,
        dry_run=True,
    )
    assert "DRY RUN" in report
    engine = get_engine(target)
    with engine.connect() as conn:
        for table in (models.snapshots, models.accounts, models.transactions):
            assert conn.execute(select(table)).first() is None


def test_missing_mapping_entry_rejected(
    tmp_path, legacy_spending_db, legacy_finances_db
):
    path = tmp_path / "partial.yaml"
    path.write_text(
        yaml.safe_dump(
            {"spending_accounts": [{"name": "Chase Checking", "snapshot": "mike"}]}
        )
    )
    with pytest.raises(MigrationError, match="Venmo"):
        apply_migration(
            legacy_spending_db,
            legacy_finances_db,
            load_mapping(path),
            tmp_path / "t.db",
        )


def test_mapping_requires_exactly_one_action(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "spending_accounts": [
                    {
                        "name": "X",
                        "snapshot": "mike",
                        "merge_into": {"snapshot": "mike", "name": "X"},
                    }
                ]
            }
        )
    )
    with pytest.raises(MigrationError, match="exactly one"):
        load_mapping(path)


def test_duplicate_fin_account_names_disambiguated(
    tmp_path, legacy_spending_db, legacy_finances_db, mapping_all
):
    """Legacy finances allowed duplicate names per snapshot; unified schema doesn't."""
    db = sqlite3.connect(legacy_finances_db)
    db.execute(
        "INSERT INTO fin_accounts VALUES (13, 1, 'Chase Checking', 'checking',"
        " 50.00, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,"
        " 'Westerra', NULL, 3)"
    )
    db.commit()
    db.close()

    template = render_mapping_template(legacy_spending_db, legacy_finances_db)
    # the qualified names appear as merge candidates / targets
    assert (
        "Chase Checking (Chase)" in template or "Chase Checking (Westerra)" in template
    )

    # the original merge target name is now qualified; update the mapping
    mapping = load_mapping(mapping_all)
    mapping["Chase Checking"]["merge_into"]["name"] = "Chase Checking (Chase)"
    target = tmp_path / "fintrack.db"
    report = apply_migration(legacy_spending_db, legacy_finances_db, mapping, target)
    assert "duplicate account name" in report

    engine = get_engine(target)
    with engine.connect() as conn:
        names = {r[0] for r in conn.execute(select(models.accounts.c.name)).all()}
        # id=10 (Chase Checking, partial_account_number '1234') gets both the
        # institution qualifier from disambiguation and the folded partial
        # number appended, since the column that used to hold it is gone.
        assert "Chase Checking (Chase) [1234]" in names
        assert "Chase Checking (Westerra)" in names


def test_nonempty_target_rejected(
    tmp_path, legacy_spending_db, legacy_finances_db, mapping_all
):
    target = tmp_path / "fintrack.db"
    apply_migration(
        legacy_spending_db, legacy_finances_db, load_mapping(mapping_all), target
    )
    with pytest.raises(MigrationError, match="not empty"):
        apply_migration(
            legacy_spending_db, legacy_finances_db, load_mapping(mapping_all), target
        )
