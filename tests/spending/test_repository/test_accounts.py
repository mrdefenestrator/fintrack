from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from spending.repository.accounts import (
    add_account,
    delete_account,
    edit_account,
    get_account_by_name,
    list_accounts,
)
from spending.repository.imports import (
    confirm_import,
    create_import,
    insert_transactions,
)


def test_add_and_list_accounts(conn):
    add_account(
        conn, name="Chase Visa", institution="Chase", account_type="credit_card"
    )
    add_account(conn, name="BofA Checking", institution="BofA", account_type="checking")
    accts = list_accounts(conn)
    assert len(accts) == 2
    assert accts[0]["name"] == "BofA Checking"


def test_add_duplicate_name_fails(conn):
    add_account(
        conn, name="Chase Visa", institution="Chase", account_type="credit_card"
    )
    with pytest.raises(IntegrityError):
        add_account(
            conn, name="Chase Visa", institution="Chase", account_type="credit_card"
        )


def test_get_account_by_name(conn):
    add_account(
        conn, name="Chase Visa", institution="Chase", account_type="credit_card"
    )
    acct = get_account_by_name(conn, "Chase Visa")
    assert acct is not None
    assert acct["institution"] == "Chase"


def test_get_account_by_name_not_found(conn):
    acct = get_account_by_name(conn, "Nonexistent")
    assert acct is None


def test_edit_account(conn):
    add_account(
        conn, name="Chase Visa", institution="Chase", account_type="credit_card"
    )
    acct = get_account_by_name(conn, "Chase Visa")
    edit_account(conn, acct["id"], name="Chase Freedom")
    updated = get_account_by_name(conn, "Chase Freedom")
    assert updated is not None


def test_delete_account(conn):
    add_account(
        conn, name="Chase Visa", institution="Chase", account_type="credit_card"
    )
    acct = get_account_by_name(conn, "Chase Visa")
    delete_account(conn, acct["id"])
    assert list_accounts(conn) == []


def test_list_accounts_latest_txn_date(conn):
    acct_id = add_account(
        conn, name="Chase Visa", institution="Chase", account_type="credit_card"
    )
    accts = list_accounts(conn)
    assert accts[0]["latest_txn_date"] is None

    import_id = create_import(
        conn, account_id=acct_id, filename="test.ofx", file_hash="abc123"
    )
    insert_transactions(
        conn,
        import_id=import_id,
        account_id=acct_id,
        transactions_data=[
            {
                "date": date(2026, 4, 1),
                "amount": "-10.00",
                "raw_description": "Coffee",
                "normalized_merchant": "coffee shop",
                "fingerprint": "fp1",
            },
            {
                "date": date(2026, 4, 15),
                "amount": "-20.00",
                "raw_description": "Gas",
                "normalized_merchant": "gas station",
                "fingerprint": "fp2",
            },
        ],
    )
    accts = list_accounts(conn)
    assert accts[0]["latest_txn_date"] == date(2026, 4, 15)


def test_list_accounts_latest_import_at(conn):
    acct_id = add_account(
        conn, name="Chase Visa", institution="Chase", account_type="credit_card"
    )
    assert list_accounts(conn)[0]["latest_import_at"] is None

    import_id = create_import(
        conn, account_id=acct_id, filename="test.ofx", file_hash="abc123"
    )
    # staging import does not count
    assert list_accounts(conn)[0]["latest_import_at"] is None

    confirm_import(conn, import_id)
    assert list_accounts(conn)[0]["latest_import_at"] is not None
