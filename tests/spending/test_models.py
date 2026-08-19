from sqlalchemy import inspect


def test_all_tables_created(engine):
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    expected = {
        "snapshots",
        "accounts",
        "imports",
        "transactions",
        "merchant_cache",
        "transaction_corrections",
        "categories",
        "budget_entries",
        "asset_entries",
        "balance_history",
        "price_cache",
    }
    assert expected == table_names


def test_transactions_has_normalized_merchant(engine):
    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("transactions")}
    assert "normalized_merchant" in columns
    assert "raw_description" in columns
    assert "fingerprint" in columns
