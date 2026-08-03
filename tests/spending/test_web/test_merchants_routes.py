"""Tests for the merchants blueprint spreadsheet inline-edit endpoints
(web/routes/merchants.py): clicking the Category cell opens an inline select,
which saves the merchant-wide category via the merchant cache.
"""

from datetime import date

import pytest
from sqlalchemy import create_engine, insert, select

from fintrack.core.db import init_db
from fintrack.core.models import accounts, imports, merchant_cache, transactions
from fintrack.ledger.repository.merchants import (
    get_cached_category,
    set_merchant_category,
)
from fintrack.snapshots.repository import create_snapshot


@pytest.fixture()
def db_engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    return engine


@pytest.fixture()
def merchant_id(db_engine):
    """Seed a merchant with a confirmed transaction in the snapshot."""
    with db_engine.connect() as conn:
        snapshot_id = create_snapshot(conn, "ledger")
        set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")

        acc_id = conn.execute(
            insert(accounts).values(
                snapshot_id=snapshot_id,
                name="Checking",
                account_type="checking",
            )
        ).inserted_primary_key[0]
        imp_id = conn.execute(
            insert(imports).values(
                account_id=acc_id,
                filename="test.ofx",
                file_hash="abc",
                status="confirmed",
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(transactions).values(
                import_id=imp_id,
                account_id=acc_id,
                date=date(2025, 1, 15),
                amount=-42,
                raw_description="WHOLE FOODS #123",
                normalized_merchant="WHOLE FOODS",
                fingerprint="fp1",
            )
        )
        conn.commit()

        mid = conn.execute(
            select(merchant_cache.c.id).where(
                merchant_cache.c.merchant_name == "WHOLE FOODS"
            )
        ).scalar()
    return mid


@pytest.fixture()
def client(db_engine):
    from web.app import create_app

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = db_engine
    with app.test_client() as c:
        yield c


def test_cell_edit_returns_category_select(client, merchant_id):
    resp = client.get(f"/s/ledger/merchants/{merchant_id}/cell?field=category")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'name="value"' in body  # spreadsheet input name
    assert "<select" in body
    assert 'name="field"' in body and 'value="category"' in body


def test_cell_edit_non_editable_field_returns_plain_row(client, merchant_id):
    resp = client.get(f"/s/ledger/merchants/{merchant_id}/cell?field=source")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Not editable → no inline editor opened
    assert "<select" not in body
    assert f'id="merchant-row-{merchant_id}"' in body


def test_cell_edit_unknown_merchant_404(client):
    resp = client.get("/s/ledger/merchants/999999/cell?field=category")
    assert resp.status_code == 404


def test_update_category_persists_and_returns_row(client, merchant_id, db_engine):
    resp = client.post(
        f"/s/ledger/merchants/{merchant_id}/category",
        data={"field": "category", "value": "Dining"},
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f'id="merchant-row-{merchant_id}"' in body
    assert "Dining" in body
    with db_engine.connect() as conn:
        assert get_cached_category(conn, "WHOLE FOODS") == "Dining"


def test_update_category_sets_source_manual(client, merchant_id, db_engine):
    client.post(
        f"/s/ledger/merchants/{merchant_id}/category",
        data={"field": "category", "value": "Dining"},
    )
    from fintrack.core.models import merchant_cache
    from sqlalchemy import select

    with db_engine.connect() as conn:
        source = conn.execute(
            select(merchant_cache.c.source).where(
                merchant_cache.c.merchant_name == "WHOLE FOODS"
            )
        ).scalar()
    assert source == "manual"


def test_row_endpoint_returns_display_row(client, merchant_id):
    resp = client.get(f"/s/ledger/merchants/{merchant_id}/row")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f'id="merchant-row-{merchant_id}"' in body
    assert "<select" not in body  # display, not editing
