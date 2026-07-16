from datetime import date
from decimal import Decimal

from spending.repository.accounts import add_account
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
            {
                "date": date(2024, 1, 20),
                "amount": Decimal("-12.99"),
                "raw_description": "NETFLIX.COM",
                "normalized_merchant": "NETFLIX",
                "fingerprint": "fp2",
            },
        ],
    )
    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    set_merchant_category(conn, "NETFLIX", "Subscriptions", source="api")
    return acct_id


def test_get_transactions_returns_resolved_fields(conn):
    _seed(conn)
    txns = get_transactions(conn, year=2024, month=1)
    assert len(txns) == 2
    wf = next(t for t in txns if t["merchant"] == "WHOLE FOODS")
    assert wf["category"] == "Groceries"


def test_get_transactions_uncategorized(conn):
    acct_id = add_account(
        conn, name="Chase", institution="Chase", account_type="credit_card"
    )
    imp_id = create_import(conn, account_id=acct_id, filename="t.ofx", file_hash="h2")
    confirm_import(conn, imp_id)
    insert_transactions(
        conn,
        import_id=imp_id,
        account_id=acct_id,
        transactions_data=[
            {
                "date": date(2024, 1, 15),
                "amount": Decimal("-10.00"),
                "raw_description": "UNKNOWN SHOP",
                "normalized_merchant": "UNKNOWN SHOP",
                "fingerprint": "fp3",
            },
        ],
    )
    txns = get_transactions(conn, year=2024, month=1)
    assert txns[0]["category"] == "Uncategorized"


def test_get_transactions_filter_by_category(conn):
    _seed(conn)
    txns = get_transactions(conn, year=2024, month=1, category="Groceries")
    assert len(txns) == 1
    assert txns[0]["merchant"] == "WHOLE FOODS"
