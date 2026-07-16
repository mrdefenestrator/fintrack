from datetime import date
from decimal import Decimal

from sqlalchemy import select

from spending.models import imports
from spending.repository.accounts import add_account
from spending.repository.imports import (
    check_file_hash,
    confirm_import,
    create_import,
    get_existing_fingerprints,
    get_staging_imports,
    get_staging_transactions,
    insert_transactions,
    reject_import,
)


def test_create_import(conn):
    acct_id = add_account(
        conn, name="Chase", institution="Chase", account_type="credit_card"
    )
    imp_id = create_import(
        conn, account_id=acct_id, filename="test.ofx", file_hash="abc123"
    )
    assert imp_id > 0


def test_check_file_hash_not_exists(conn):
    assert check_file_hash(conn, "abc123") is False


def test_check_file_hash_exists(conn):
    acct_id = add_account(
        conn, name="Chase", institution="Chase", account_type="credit_card"
    )
    create_import(conn, account_id=acct_id, filename="test.ofx", file_hash="abc123")
    assert check_file_hash(conn, "abc123") is True


def test_insert_and_get_transactions(conn):
    acct_id = add_account(
        conn, name="Chase", institution="Chase", account_type="credit_card"
    )
    imp_id = create_import(
        conn, account_id=acct_id, filename="test.ofx", file_hash="abc123"
    )
    insert_transactions(
        conn,
        import_id=imp_id,
        account_id=acct_id,
        transactions_data=[
            {
                "date": date(2024, 1, 15),
                "amount": Decimal("-42.50"),
                "raw_description": "WHOLE FOODS #1234",
                "normalized_merchant": "WHOLE FOODS",
                "fingerprint": "fp1",
            }
        ],
    )
    fps = get_existing_fingerprints(conn, acct_id)
    assert "fp1" in fps


def test_confirm_import(conn):
    acct_id = add_account(
        conn, name="Chase", institution="Chase", account_type="credit_card"
    )
    imp_id = create_import(
        conn, account_id=acct_id, filename="test.ofx", file_hash="abc123"
    )
    confirm_import(conn, imp_id)
    staging = get_staging_imports(conn)
    assert len(staging) == 0


def test_reject_import(conn):
    acct_id = add_account(
        conn, name="Chase", institution="Chase", account_type="credit_card"
    )
    imp_id = create_import(
        conn, account_id=acct_id, filename="test.ofx", file_hash="abc123"
    )
    insert_transactions(
        conn,
        import_id=imp_id,
        account_id=acct_id,
        transactions_data=[
            {
                "date": date(2024, 1, 15),
                "amount": Decimal("-42.50"),
                "raw_description": "WHOLE FOODS",
                "normalized_merchant": "WHOLE FOODS",
                "fingerprint": "fp-reject-1",
            }
        ],
    )
    reject_import(conn, imp_id)
    assert len(get_staging_imports(conn)) == 0
    assert get_staging_transactions(conn, imp_id) == []
    assert "fp-reject-1" not in get_existing_fingerprints(conn, acct_id)


def test_create_import_stores_ledger_balance(conn):
    acct_id = add_account(
        conn, name="Chase", institution="Chase", account_type="credit_card"
    )
    imp_id = create_import(
        conn,
        account_id=acct_id,
        filename="test.ofx",
        file_hash="bal001",
        ledger_balance=Decimal("1234.56"),
        ledger_balance_date=date(2026, 1, 31),
    )
    row = conn.execute(select(imports).where(imports.c.id == imp_id)).fetchone()
    assert row.ledger_balance == Decimal("1234.56")
    assert row.ledger_balance_date == date(2026, 1, 31)


def test_create_import_stores_available_balance(conn):
    acct_id = add_account(
        conn, name="Chase", institution="Chase", account_type="credit_card"
    )
    imp_id = create_import(
        conn,
        account_id=acct_id,
        filename="test.ofx",
        file_hash="bal002",
        available_balance=Decimal("8765.44"),
        available_balance_date=date(2026, 1, 31),
    )
    row = conn.execute(select(imports).where(imports.c.id == imp_id)).fetchone()
    assert row.available_balance == Decimal("8765.44")
    assert row.available_balance_date == date(2026, 1, 31)


def test_create_import_stores_beginning_balance(conn):
    acct_id = add_account(
        conn, name="Chase", institution="Chase", account_type="credit_card"
    )
    imp_id = create_import(
        conn,
        account_id=acct_id,
        filename="test.csv",
        file_hash="bal003",
        beginning_balance=Decimal("0.00"),
        ledger_balance=Decimal("237.00"),
    )
    row = conn.execute(select(imports).where(imports.c.id == imp_id)).fetchone()
    assert row.beginning_balance == Decimal("0.00")
    assert row.ledger_balance == Decimal("237.00")


def test_create_import_balance_fields_default_to_null(conn):
    acct_id = add_account(
        conn, name="Chase", institution="Chase", account_type="credit_card"
    )
    imp_id = create_import(
        conn, account_id=acct_id, filename="test.ofx", file_hash="bal004"
    )
    row = conn.execute(select(imports).where(imports.c.id == imp_id)).fetchone()
    assert row.ledger_balance is None
    assert row.available_balance is None
    assert row.beginning_balance is None


def test_check_file_hash_ignored_after_reject(conn):
    acct_id = add_account(
        conn, name="Chase", institution="Chase", account_type="credit_card"
    )
    imp_id = create_import(
        conn, account_id=acct_id, filename="test.ofx", file_hash="abc123"
    )
    reject_import(conn, imp_id)
    assert check_file_hash(conn, "abc123") is False
