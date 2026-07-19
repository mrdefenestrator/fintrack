from datetime import date
from decimal import Decimal

from fintrack.ledger.repository.accounts import add_account
from fintrack.ledger.repository.imports import (
    confirm_import,
    create_import,
    insert_transactions,
)
from fintrack.ledger.repository.merchants import set_merchant_category
from fintrack.ledger.repository.transactions import get_transactions


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


def test_get_transactions_filter_by_categories_multi(conn):
    _seed(conn)  # WHOLE FOODS=Groceries, NETFLIX=Subscriptions
    both = get_transactions(
        conn, year=2024, month=1, categories=["Groceries", "Subscriptions"]
    )
    assert {t["merchant"] for t in both} == {"WHOLE FOODS", "NETFLIX"}
    one = get_transactions(conn, year=2024, month=1, categories=["Subscriptions"])
    assert [t["merchant"] for t in one] == ["NETFLIX"]


def test_get_transactions_filter_by_account_ids_multi(conn):
    acct1 = _seed(conn)  # Chase, 2 transactions
    acct2 = add_account(conn, name="BofA", institution="BofA", account_type="checking")
    imp2 = create_import(conn, account_id=acct2, filename="b.ofx", file_hash="hb")
    confirm_import(conn, imp2)
    insert_transactions(
        conn,
        import_id=imp2,
        account_id=acct2,
        transactions_data=[
            {
                "date": date(2024, 1, 10),
                "amount": Decimal("-5.00"),
                "raw_description": "COFFEE",
                "normalized_merchant": "COFFEE",
                "fingerprint": "fpc",
            }
        ],
    )
    both = get_transactions(conn, year=2024, month=1, account_ids=[acct1, acct2])
    assert len(both) == 3
    one = get_transactions(conn, year=2024, month=1, account_ids=[acct2])
    assert [t["merchant"] for t in one] == ["COFFEE"]


def _seed_transfer_pair(conn):
    """Seed a matching pair of transfer legs plus an unrelated transaction."""
    acct_id = add_account(
        conn, name="Chase", institution="Chase", account_type="checking"
    )
    imp_id = create_import(conn, account_id=acct_id, filename="t2.ofx", file_hash="h3")
    confirm_import(conn, imp_id)
    insert_transactions(
        conn,
        import_id=imp_id,
        account_id=acct_id,
        transactions_data=[
            {
                "date": date(2024, 1, 5),
                "amount": Decimal("-500.00"),
                "raw_description": "TRANSFER TO SAVINGS",
                "normalized_merchant": "TRANSFER",
                "fingerprint": "fp10",
            },
            {
                "date": date(2024, 1, 5),
                "amount": Decimal("500.00"),
                "raw_description": "TRANSFER FROM CHECKING",
                "normalized_merchant": "TRANSFER",
                "fingerprint": "fp11",
            },
            {
                "date": date(2024, 1, 6),
                "amount": Decimal("-100.00"),
                "raw_description": "OTHER SHOP",
                "normalized_merchant": "OTHER SHOP",
                "fingerprint": "fp12",
            },
        ],
    )
    return acct_id


def test_get_transactions_amount_tolerance_matches_both_signs(conn):
    _seed_transfer_pair(conn)
    txns = get_transactions(conn, year=2024, month=1, amount="500")
    assert len(txns) == 2
    amounts = {t["amount"] for t in txns}
    assert amounts == {Decimal("-500.00"), Decimal("500.00")}


def test_get_transactions_amount_tolerance_within_half_dollar(conn):
    _seed_transfer_pair(conn)
    txns = get_transactions(conn, year=2024, month=1, amount="500.40")
    assert len(txns) == 2


def test_get_transactions_amount_tolerance_excludes_outside_range(conn):
    _seed_transfer_pair(conn)
    txns = get_transactions(conn, year=2024, month=1, amount="500.60")
    assert len(txns) == 0


def test_get_transactions_amount_sign_sensitive_negative(conn):
    _seed_transfer_pair(conn)
    txns = get_transactions(conn, year=2024, month=1, amount="-500")
    assert len(txns) == 1
    assert txns[0]["amount"] == Decimal("-500.00")


def test_get_transactions_amount_sign_sensitive_positive(conn):
    _seed_transfer_pair(conn)
    txns = get_transactions(conn, year=2024, month=1, amount="+500")
    assert len(txns) == 1
    assert txns[0]["amount"] == Decimal("500.00")


def test_get_transactions_amount_range(conn):
    _seed_transfer_pair(conn)
    txns = get_transactions(conn, year=2024, month=1, amount="50-200")
    assert len(txns) == 1
    assert txns[0]["raw_description"] == "OTHER SHOP"


def test_get_transactions_amount_greater_than(conn):
    _seed_transfer_pair(conn)
    txns = get_transactions(conn, year=2024, month=1, amount=">200")
    assert len(txns) == 2
    assert all(abs(t["amount"]) > 200 for t in txns)


def test_get_transactions_amount_less_than_or_equal(conn):
    _seed_transfer_pair(conn)
    txns = get_transactions(conn, year=2024, month=1, amount="<=100")
    assert len(txns) == 1
    assert txns[0]["raw_description"] == "OTHER SHOP"


def test_get_transactions_amount_invalid_is_ignored(conn):
    _seed_transfer_pair(conn)
    txns = get_transactions(conn, year=2024, month=1, amount="not-a-number")
    assert len(txns) == 3


def test_get_transactions_amount_combines_with_category_filter(conn):
    _seed(conn)
    # -42.50 (Groceries) and -12.99 (Subscriptions) both seeded in Jan 2024.
    txns = get_transactions(
        conn, year=2024, month=1, amount="40-50", category="Groceries"
    )
    assert len(txns) == 1
    assert txns[0]["merchant"] == "WHOLE FOODS"

    txns_wrong_category = get_transactions(
        conn, year=2024, month=1, amount="40-50", category="Subscriptions"
    )
    assert len(txns_wrong_category) == 0
