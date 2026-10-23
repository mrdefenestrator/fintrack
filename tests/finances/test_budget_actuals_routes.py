"""Budget vs Actual page (issues #53, #73): /budget/actual surfaces per-entry
expected-vs-realized for a month, on its own page rather than the Budget sheet."""

import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, insert, select

from fintrack.accounts.repository import add_account
from fintrack.budget.reconcile import link_transaction
from fintrack.budget.repository import add_budget_entry, get_budget_entries
from fintrack.core.db import init_db
from fintrack.core.models import imports, transactions
from fintrack.ledger.repository.corrections import get_correction
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


def test_month_params_select_month_and_pager(client, db_engine):
    _add_rent(db_engine)
    body = client.get("/s/home/budget/actual?year=2025&month=1").get_data(as_text=True)
    assert "Jan 2025" in body
    assert "year=2024&amp;month=12" in body
    assert "year=2025&amp;month=2" in body


def test_bad_month_param_falls_back_to_current(client, db_engine):
    _add_rent(db_engine)
    resp = client.get("/s/home/budget/actual?year=2025&month=13")
    assert resp.status_code == 200
    today = datetime.now().astimezone().date()
    assert f"{today:%b %Y}" in resp.get_data(as_text=True)


def test_htmx_request_renders_partial(client, db_engine):
    _add_rent(db_engine)
    body = client.get("/s/home/budget/actual", headers={"HX-Request": "true"}).get_data(
        as_text=True
    )
    assert "budget-actual-table" in body
    assert "<html" not in body


def _add_entries(db_engine):
    with db_engine.connect() as conn:
        sid = create_snapshot(conn, "home")
        for kind, desc, amount, cat in [
            ("expense", "Rent", 2000, "Housing"),
            ("expense", "Netflix", 15, "Entertainment"),
            ("income", "Paycheck", 4000, "Income"),
        ]:
            add_budget_entry(
                conn,
                sid,
                {
                    "kind": kind,
                    "description": desc,
                    "amount": amount,
                    "recurrence": "monthly",
                    "category": cat,
                },
            )


def test_filters_narrow_rows(client, db_engine):
    _add_entries(db_engine)
    body = client.get("/s/home/budget/actual?kind=income").get_data(as_text=True)
    assert "Paycheck" in body
    assert "Netflix" not in body
    body = client.get("/s/home/budget/actual?category=Housing").get_data(as_text=True)
    assert "Rent" in body
    assert "Paycheck" not in body
    body = client.get("/s/home/budget/actual?status=matched").get_data(as_text=True)
    assert "No matching budget entries" in body


def test_total_row_is_net_income_minus_expenses(client, db_engine):
    _add_entries(db_engine)
    body = client.get("/s/home/budget/actual").get_data(as_text=True)
    # Expected: 4000 - 2000 - 15; nothing realized, so delta is the negation.
    assert "3 entries · net" in body
    assert "+$1,985.00" in body
    assert "-$1,985.00" in body


def test_page_empty_state(client, db_engine):
    with db_engine.connect() as conn:
        create_snapshot(conn, "home")
    body = client.get("/s/home/budget/actual").get_data(as_text=True)
    assert "No active budget entries" in body


# --- Link suggestions (issue #70) -------------------------------------------


def _rent_with_unlinked_charge(db_engine):
    """Snapshot "home" with a Rent entry and one matching unlinked charge in
    May 2026. Returns (snapshot_id, entry_ref, txn_id)."""
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
                "dayOfMonth": 1,
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
                date=date(2026, 5, 1),
                amount="-2000.00",
                raw_description="Landlord",
                normalized_merchant="Landlord",
                fingerprint=uuid.uuid4().hex,
            )
        ).inserted_primary_key[0]
        conn.commit()
    return sid, ref, txn_id


_MAY = "/s/home/budget/actual?year=2026&month=5"


def test_view_mode_shows_suggestion_count_linking_to_edit_mode(client, db_engine):
    _rent_with_unlinked_charge(db_engine)
    body = client.get(_MAY).get_data(as_text=True)
    assert "1 suggested link" in body
    assert "year=2026&amp;month=5&amp;edit=1" in body
    # Actions are edit-mode only.
    assert "data-suggestion-apply" not in body


def test_edit_mode_lists_suggestion_under_entry(client, db_engine):
    _, ref, txn_id = _rent_with_unlinked_charge(db_engine)
    body = client.get(_MAY + "&edit=1").get_data(as_text=True)
    assert f'data-suggestions-for="{ref}"' in body
    assert f'data-transaction-id="{txn_id}"' in body
    assert "Landlord" in body
    assert "data-suggestion-apply" in body
    assert "data-suggestion-dismiss" in body


def test_apply_links_and_rerenders_month(client, db_engine):
    _, ref, txn_id = _rent_with_unlinked_charge(db_engine)
    resp = client.post(
        "/s/home/budget/actual/link?year=2026&month=5&edit=1",
        data={"transaction_id": txn_id, "budget_entry_ref": ref},
        headers={"HX-Request": "true"},
    )
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "May 2026" in body
    assert "Matched" in body
    assert "data-suggestion-apply" not in body
    with db_engine.connect() as conn:
        assert get_correction(conn, txn_id)["budget_entry_ref"] == ref


def test_dismiss_hides_suggestion_without_linking(client, db_engine):
    _, ref, txn_id = _rent_with_unlinked_charge(db_engine)
    resp = client.post(
        "/s/home/budget/actual/dismiss?year=2026&month=5&edit=1",
        data={"transaction_id": txn_id, "budget_entry_ref": ref},
        headers={"HX-Request": "true"},
    )
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "data-suggestion-dismiss" not in body
    assert "suggested link" not in body
    with db_engine.connect() as conn:
        assert get_correction(conn, txn_id) is None
    # Stays dismissed on a fresh load.
    assert "suggested link" not in client.get(_MAY).get_data(as_text=True)


def test_suggestion_actions_reject_bad_input(client, db_engine):
    _, _, txn_id = _rent_with_unlinked_charge(db_engine)
    url = "/s/home/budget/actual/link"
    assert client.post(url, data={"transaction_id": "x"}).status_code == 422
    # Unknown entry → SnapshotMismatch → 422, nothing linked.
    resp = client.post(url, data={"transaction_id": txn_id, "budget_entry_ref": 999})
    assert resp.status_code == 422
    with db_engine.connect() as conn:
        other = create_snapshot(conn, "other")
        add_budget_entry(
            conn,
            other,
            {
                "kind": "expense",
                "description": "X",
                "amount": 1,
                "recurrence": "monthly",
            },
        )
        foreign = get_budget_entries(conn, other)[-1]["_db_id"]
    resp = client.post(
        "/s/home/budget/actual/dismiss",
        data={"transaction_id": txn_id, "budget_entry_ref": foreign},
    )
    assert resp.status_code == 422


def test_actual_cell_notes_netted_refund(client, db_engine):
    sid, ref, txn_id = _rent_with_unlinked_charge(db_engine)
    with db_engine.connect() as conn:
        link_transaction(conn, sid, txn_id, ref)
        acct = conn.execute(
            select(transactions.c.account_id).where(transactions.c.id == txn_id)
        ).scalar()
        imp = conn.execute(
            select(transactions.c.import_id).where(transactions.c.id == txn_id)
        ).scalar()
        refund = conn.execute(
            insert(transactions).values(
                import_id=imp,
                account_id=acct,
                date=date(2026, 5, 20),
                amount="200.00",
                raw_description="Landlord",
                normalized_merchant="Landlord",
                fingerprint=uuid.uuid4().hex,
            )
        ).inserted_primary_key[0]
        conn.commit()
        link_transaction(conn, sid, refund, ref)
    body = client.get(_MAY).get_data(as_text=True)
    assert "data-actual-reversed" in body
    assert "−$200.00 refund" in body
    assert "$1,800.00" in body
