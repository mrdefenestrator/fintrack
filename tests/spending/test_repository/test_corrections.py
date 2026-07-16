from datetime import date
from decimal import Decimal

from spending.repository.accounts import add_account
from spending.repository.corrections import (
    apply_transaction_correction,
    get_correction,
)
from spending.repository.imports import (
    confirm_import,
    create_import,
    insert_transactions,
)
from spending.repository.merchants import set_merchant_category
from spending.repository.transactions import get_transactions


def _seed(conn):
    acct_id = add_account(
        conn, name="Chase", institution="Chase", account_type="credit_card"
    )
    imp_id = create_import(conn, account_id=acct_id, filename="t.ofx", file_hash="h1")
    confirm_import(conn, imp_id)
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
            },
        ],
    )
    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    txns = get_transactions(conn, year=2024, month=1)
    return txns[0]["id"]


def test_apply_category_correction(conn):
    txn_id = _seed(conn)
    apply_transaction_correction(conn, txn_id, category="Shopping")
    txns = get_transactions(conn, year=2024, month=1)
    assert txns[0]["category"] == "Shopping"


def test_apply_merchant_correction(conn):
    txn_id = _seed(conn)
    apply_transaction_correction(conn, txn_id, merchant_name="WHOLE FOODS MARKET")
    txns = get_transactions(conn, year=2024, month=1)
    assert txns[0]["merchant"] == "WHOLE FOODS MARKET"


def test_get_correction(conn):
    txn_id = _seed(conn)
    apply_transaction_correction(
        conn, txn_id, category="Shopping", notes="Miscategorized"
    )
    correction = get_correction(conn, txn_id)
    assert correction is not None
    assert correction["category"] == "Shopping"
    assert correction["notes"] == "Miscategorized"


def test_correction_updates_existing(conn):
    txn_id = _seed(conn)
    apply_transaction_correction(conn, txn_id, category="Shopping")
    apply_transaction_correction(conn, txn_id, category="Entertainment")
    txns = get_transactions(conn, year=2024, month=1)
    assert txns[0]["category"] == "Entertainment"
