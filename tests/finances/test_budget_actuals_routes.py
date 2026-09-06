"""Budget page budget-vs-actual panel (issue #53): the /budget view surfaces
per-entry expected-vs-realized for the current month."""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine, insert, select

from fintrack.accounts.repository import add_account
from fintrack.budget.reconcile import link_transaction
from fintrack.budget.repository import add_budget_entry, get_budget_entries
from fintrack.core.db import init_db
from fintrack.core.models import imports, transactions
from fintrack.snapshots.repository import create_snapshot


@pytest.fixture()
def db_engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    return engine


@pytest.fixture()
def client(db_engine):
    from web.app import create_app

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = db_engine
    with app.test_client() as c:
        yield c


def test_panel_lists_active_entries(client, db_engine):
    with db_engine.connect() as conn:
        sid = create_snapshot(conn, "home")
        add_budget_entry(
            conn,
            sid,
            {
                "kind": "expense",
                "description": "Rent",
                "amount": 2000,
                "recurrence": "monthly",
            },
        )
    resp = client.get("/s/home/budget")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Budget vs actual" in body
    assert "Rent" in body


def test_panel_reflects_linked_transaction_as_matched(client, db_engine):
    today = datetime.now().astimezone().date()
    with db_engine.connect() as conn:
        sid = create_snapshot(conn, "home")
        acct = add_account(conn, sid, {"name": "Checking", "type": "checking"})
        add_budget_entry(
            conn,
            sid,
            {
                "kind": "expense",
                "description": "Rent",
                "amount": 2000,
                "recurrence": "monthly",
            },
        )
        ref = get_budget_entries(conn, sid)[-1]["_db_id"]
        imp = conn.execute(
            insert(imports).values(
                account_id=acct,
                holding_group="cash",
                filename="s.ofx",
                file_hash=uuid.uuid4().hex,
                status="confirmed",
            )
        ).inserted_primary_key[0]
        txn_id = conn.execute(
            insert(transactions).values(
                import_id=imp,
                account_id=acct,
                date=today,
                amount="-2000.00",
                raw_description="Landlord",
                normalized_merchant="Landlord",
                fingerprint=uuid.uuid4().hex,
            )
        ).inserted_primary_key[0]
        conn.commit()
        link_transaction(conn, sid, txn_id, ref)
        assert conn.execute(
            select(transactions.c.id).where(transactions.c.id == txn_id)
        ).scalar()

    resp = client.get("/s/home/budget")
    assert resp.status_code == 200
    assert "Matched" in resp.get_data(as_text=True)
