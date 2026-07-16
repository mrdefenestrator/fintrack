from datetime import date
from decimal import Decimal

from spending.repository.accounts import add_account
from spending.repository.aggregations import get_monthly_category_totals
from spending.repository.imports import (
    confirm_import,
    create_import,
    insert_transactions,
)
from spending.repository.merchants import set_merchant_category


def _seed_transactions(conn):
    """Helper: create account, import, and sample transactions."""
    acct_id = add_account(
        conn, name="Chase", institution="Chase", account_type="credit_card"
    )
    imp_id = create_import(
        conn, account_id=acct_id, filename="test.ofx", file_hash="abc"
    )
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
                "date": date(2024, 1, 16),
                "amount": Decimal("-15.00"),
                "raw_description": "WHOLE FOODS #5678",
                "normalized_merchant": "WHOLE FOODS",
                "fingerprint": "fp2",
            },
            {
                "date": date(2024, 1, 20),
                "amount": Decimal("-12.99"),
                "raw_description": "NETFLIX",
                "normalized_merchant": "NETFLIX",
                "fingerprint": "fp3",
            },
            {
                "date": date(2024, 2, 10),
                "amount": Decimal("-50.00"),
                "raw_description": "WHOLE FOODS #9999",
                "normalized_merchant": "WHOLE FOODS",
                "fingerprint": "fp4",
            },
        ],
    )
    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    set_merchant_category(conn, "NETFLIX", "Subscriptions", source="api")
    return acct_id


def test_monthly_category_totals(conn):
    _seed_transactions(conn)
    totals = get_monthly_category_totals(conn, year=2024, month=1)
    by_cat = {row["category"]: row["total"] for row in totals}
    assert by_cat["Groceries"] == Decimal("-57.50")
    assert by_cat["Subscriptions"] == Decimal("-12.99")


def test_monthly_category_totals_different_month(conn):
    _seed_transactions(conn)
    totals = get_monthly_category_totals(conn, year=2024, month=2)
    by_cat = {row["category"]: row["total"] for row in totals}
    assert by_cat["Groceries"] == Decimal("-50.00")
    assert "Subscriptions" not in by_cat
