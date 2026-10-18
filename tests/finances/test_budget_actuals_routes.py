"""Budget vs Actual page (issues #53, #73): /budget/actual surfaces per-entry
expected-vs-realized for a month, on its own page rather than the Budget sheet."""

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


def test_page_lists_active_entries(client, db_engine):
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
    resp = client.get("/s/home/budget/actual")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Budget vs Actual" in body
    assert "Rent" in body
    # Never linked: reported as not linked, not as a missed charge.
    assert "Not linked" in body
    assert "missing</span>" not in body


def test_page_reflects_linked_transaction_as_matched(client, db_engine):
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

    resp = client.get("/s/home/budget/actual")
    assert resp.status_code == 200
    assert "Matched" in resp.get_data(as_text=True)


def _add_rent(db_engine):
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


def test_budget_sheet_no_longer_embeds_actuals(client, db_engine):
    _add_rent(db_engine)
    body = client.get("/s/home/budget").get_data(as_text=True)
    assert "budget-actual-table" not in body
    assert "Not linked" not in body
    # ...but the sidebar links to the dedicated page.
    assert 'href="/s/home/budget/actual"' in body


def test_page_marks_its_sidebar_entry_active(client, db_engine):
    _add_rent(db_engine)
    body = client.get("/s/home/budget/actual").get_data(as_text=True)
    assert 'aria-current="page">Budget vs Actual</a>' in body


def test_month_param_selects_month_and_pager(client, db_engine):
    _add_rent(db_engine)
    body = client.get("/s/home/budget/actual?month=2025-01").get_data(as_text=True)
    assert "Jan 2025" in body
    assert "month=2024-12" in body
    assert "month=2025-02" in body


def test_bad_month_param_falls_back_to_current(client, db_engine):
    _add_rent(db_engine)
    resp = client.get("/s/home/budget/actual?month=nope")
    assert resp.status_code == 200
    today = datetime.now().astimezone().date()
    assert f"{today:%b %Y}" in resp.get_data(as_text=True)


def test_page_empty_state(client, db_engine):
    with db_engine.connect() as conn:
        create_snapshot(conn, "home")
    body = client.get("/s/home/budget/actual").get_data(as_text=True)
    assert "No active budget entries" in body
