import datetime

from sqlalchemy import insert

from spending.models import accounts, imports, transactions
from spending.repository.merchants import (
    get_cached_category,
    get_uncached_merchants,
    list_merchants,
    list_merchants_with_stats,
    set_merchant_category,
)


def test_set_and_get_cached_category(conn):
    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    assert get_cached_category(conn, "WHOLE FOODS") == "Groceries"


def test_get_cached_category_miss(conn):
    assert get_cached_category(conn, "UNKNOWN") is None


def test_get_uncached_merchants(conn):
    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    uncached = get_uncached_merchants(conn, ["WHOLE FOODS", "NETFLIX", "TARGET"])
    assert set(uncached) == {"NETFLIX", "TARGET"}


def test_set_merchant_category_updates_existing(conn):
    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    set_merchant_category(conn, "WHOLE FOODS", "Shopping", source="manual")
    assert get_cached_category(conn, "WHOLE FOODS") == "Shopping"


def test_list_merchants(conn):
    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    set_merchant_category(conn, "NETFLIX", "Subscriptions", source="manual")
    merchants = list_merchants(conn)
    assert len(merchants) == 2
    names = {m["merchant_name"] for m in merchants}
    assert names == {"WHOLE FOODS", "NETFLIX"}


def test_list_merchants_with_stats_no_transactions(conn):
    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    merchants = list_merchants_with_stats(conn)
    assert len(merchants) == 1
    assert merchants[0]["merchant_name"] == "WHOLE FOODS"
    assert merchants[0]["txn_count"] == 0
    assert merchants[0]["last_seen"] is None


def test_list_merchants_with_stats_with_confirmed_transactions(conn):
    # Set up account, confirmed import, and transaction
    conn.execute(
        insert(accounts).values(
            name="Chase", institution="Chase", account_type="checking"
        )
    )
    conn.commit()
    account_id = (
        conn.execute(accounts.select().where(accounts.c.name == "Chase")).fetchone().id
    )

    conn.execute(
        insert(imports).values(
            account_id=account_id,
            filename="test.ofx",
            file_hash="abc123",
            status="confirmed",
        )
    )
    conn.commit()
    import_id = (
        conn.execute(imports.select().where(imports.c.account_id == account_id))
        .fetchone()
        .id
    )

    conn.execute(
        insert(transactions).values(
            import_id=import_id,
            account_id=account_id,
            date=datetime.date(2024, 1, 15),
            amount=-42.50,
            raw_description="WHOLE FOODS",
            normalized_merchant="WHOLE FOODS",
            fingerprint="fp1",
        )
    )
    conn.commit()

    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    merchants = list_merchants_with_stats(conn)
    assert len(merchants) == 1
    m = merchants[0]
    assert m["merchant_name"] == "WHOLE FOODS"
    assert m["txn_count"] == 1
    assert str(m["last_seen"]) == "2024-01-15"
