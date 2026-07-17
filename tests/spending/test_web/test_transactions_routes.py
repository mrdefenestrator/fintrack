"""Tests for the transactions blueprint spreadsheet inline-edit endpoints
(web/routes/transactions.py). Edits to category / merchant name / notes go
through the transaction_corrections overlay; raw imported columns are immutable.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from fintrack.core.db import init_db
from fintrack.ledger.repository.accounts import add_account
from fintrack.ledger.repository.corrections import get_correction
from fintrack.ledger.repository.imports import (
    confirm_import,
    create_import,
    insert_transactions,
)
from fintrack.ledger.repository.merchants import (
    get_cached_category,
    set_merchant_category,
)
from fintrack.ledger.repository.transactions import get_transactions
from fintrack.snapshots.repository import create_snapshot


@pytest.fixture()
def db_engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    return engine


@pytest.fixture()
def seeded(db_engine):
    """One confirmed transaction (WHOLE FOODS) in the 'ledger' snapshot.

    Returns (snapshot_id, txn_id).
    """
    with db_engine.connect() as conn:
        sid = create_snapshot(conn, "ledger")
        acct_id = add_account(
            conn,
            name="Chase",
            institution="Chase",
            account_type="credit_card",
            snapshot_id=sid,
        )
        imp_id = create_import(
            conn, account_id=acct_id, filename="t.ofx", file_hash="h1"
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
            ],
        )
        set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
        txn_id = get_transactions(conn, year=2024, month=1, snapshot_id=sid)[0]["id"]
    return sid, txn_id


@pytest.fixture()
def client(db_engine):
    from web.app import create_app

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = db_engine
    with app.test_client() as c:
        yield c


# ---- cell editors ------------------------------------------------------------


def test_cell_edit_category_returns_select(client, seeded):
    _, txn_id = seeded
    resp = client.get(f"/s/ledger/transactions/{txn_id}/cell?field=category")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "<select" in body
    assert 'name="apply_to_merchant"' in body  # bulk option preserved


def test_cell_edit_merchant_returns_text_input(client, seeded):
    _, txn_id = seeded
    resp = client.get(f"/s/ledger/transactions/{txn_id}/cell?field=merchant_name")
    body = resp.get_data(as_text=True)
    assert 'name="value"' in body
    assert 'value="WHOLE FOODS"' in body


def test_cell_edit_notes_returns_text_input(client, seeded):
    _, txn_id = seeded
    resp = client.get(f"/s/ledger/transactions/{txn_id}/cell?field=notes")
    body = resp.get_data(as_text=True)
    assert 'name="field"' in body and 'value="notes"' in body


def test_cell_edit_immutable_field_not_editable(client, seeded):
    _, txn_id = seeded
    resp = client.get(f"/s/ledger/transactions/{txn_id}/cell?field=amount")
    assert resp.status_code == 200
    # Raw field is not opened for editing
    assert "table-cell-input" not in resp.get_data(as_text=True)


# ---- updates through the corrections overlay ---------------------------------


def test_update_category_writes_correction(client, seeded, db_engine):
    _, txn_id = seeded
    resp = client.post(
        f"/s/ledger/transactions/{txn_id}/update",
        data={"field": "category", "value": "Dining"},
    )
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        assert get_correction(conn, txn_id)["category"] == "Dining"
        # merchant cache unchanged when apply-to-merchant is off
        assert get_cached_category(conn, "WHOLE FOODS") == "Groceries"


def test_update_merchant_name_writes_correction(client, seeded, db_engine):
    _, txn_id = seeded
    resp = client.post(
        f"/s/ledger/transactions/{txn_id}/update",
        data={"field": "merchant_name", "value": "WHOLE FOODS MARKET"},
    )
    assert resp.status_code == 200
    assert "WHOLE FOODS MARKET" in resp.get_data(as_text=True)
    with db_engine.connect() as conn:
        assert get_correction(conn, txn_id)["merchant_name"] == "WHOLE FOODS MARKET"


def test_update_notes_writes_correction(client, seeded, db_engine):
    _, txn_id = seeded
    client.post(
        f"/s/ledger/transactions/{txn_id}/update",
        data={"field": "notes", "value": "reimbursable"},
    )
    with db_engine.connect() as conn:
        assert get_correction(conn, txn_id)["notes"] == "reimbursable"


def test_update_category_apply_to_merchant_redirects_and_sets_cache(
    client, seeded, db_engine
):
    _, txn_id = seeded
    resp = client.post(
        f"/s/ledger/transactions/{txn_id}/update",
        data={"field": "category", "value": "Dining", "apply_to_merchant": "on"},
    )
    assert resp.status_code == 204
    assert resp.headers.get("HX-Redirect")
    with db_engine.connect() as conn:
        assert get_cached_category(conn, "WHOLE FOODS") == "Dining"
        # per-transaction correction NOT written in the merchant-wide branch
        assert get_correction(conn, txn_id) is None


def test_update_rejects_immutable_field(client, seeded):
    _, txn_id = seeded
    resp = client.post(
        f"/s/ledger/transactions/{txn_id}/update",
        data={"field": "amount", "value": "-1.00"},
    )
    assert resp.status_code == 422


def test_row_endpoint_returns_display_row(client, seeded):
    _, txn_id = seeded
    resp = client.get(f"/s/ledger/transactions/{txn_id}/row")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f'id="txn-row-{txn_id}"' in body
    assert "table-cell-input" not in body
