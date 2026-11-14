"""Budget vs Actual on the Budget sheet (issues #53, #73, #143): each entry's
Actual / Delta / Status for the viewed month, the Status filter, the Actual
cell's link to its transactions, the month's findings, link suggestions in
edit mode, and the redirect from the old /budget/actual page."""

import re
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


def _row(body: str, index: int) -> str:
    return body.split(f'id="budget-row-{index}"')[1].split("</tr>")[0]


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


def test_sheet_shows_never_linked_entry_as_not_linked(client, db_engine):
    _add_rent(db_engine)
    body = client.get("/s/home/budget").get_data(as_text=True)
    row = _row(body, 0)
    # Never linked: reported as not linked, not as a missed charge.
    assert 'data-status="unlinked"' in row and "Not linked" in row
    assert "data-actuals-missing" not in body
    # Nothing behind the actual, so nothing to open.
    assert "data-actual-link" not in row


def test_linked_transaction_reads_matched_and_links_to_it(client, db_engine):
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

    row = _row(client.get("/s/home/budget").get_data(as_text=True), 0)
    assert "Matched" in row
    # Actual opens the month's transactions linked to the entry.
    link = re.search(r'<a href="([^"]+)"[^>]*data-actual-link', row).group(1)
    assert link.startswith("/s/home/transactions?")
    assert f"budget={ref}" in link
    assert f"year={today.year}" in link and f"month={today.month}" in link
    assert "1 linked transaction in" in row
    assert f"Last seen {today}" in row


def test_old_page_redirects_to_the_budget_sheet(client, db_engine):
    _add_rent(db_engine)
    resp = client.get("/s/home/budget/actual?year=2025&month=1&kind=expense&edit=1")
    assert resp.status_code == 302
    loc = resp.headers["Location"]
    assert loc.startswith("/s/home/budget?")
    assert "ym=2025-01" in loc and "kind=expense" in loc and "edit=1" in loc
    assert "year=" not in loc
    assert client.get("/s/home/budget/actual").headers["Location"] == "/s/home/budget"
    # A bad month is dropped; the sheet falls back to this month.
    loc = client.get("/s/home/budget/actual?year=2025&month=13").headers["Location"]
    assert loc == "/s/home/budget"


def test_sidebar_has_no_budget_vs_actual_link(client, db_engine):
    _add_rent(db_engine)
    body = client.get("/s/home/budget").get_data(as_text=True)
    assert ">Budget vs Actual</a>" not in body
    assert 'aria-current="page">Budget</a>' in body


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


def test_status_filter_narrows_rows_and_locks_reorder(client, db_engine):
    _add_entries(db_engine)
    body = client.get("/s/home/budget?status=unlinked").get_data(as_text=True)
    assert body.count('id="budget-row-') == 3
    assert "data-reorder-locked" in body
    assert re.search(r'name="status" value="unlinked"[^>]*checked', body)
    body = client.get("/s/home/budget?status=matched&edit=1").get_data(as_text=True)
    assert 'id="budget-row-' not in body
    assert "No entries match the current filters." in body
    # The old page's ?category= filter carries over.
    body = client.get("/s/home/budget?category=Housing").get_data(as_text=True)
    assert "Rent" in body and "Paycheck" not in body


def test_net_footer_sums_actual_and_delta(client, db_engine):
    _add_entries(db_engine)
    body = client.get("/s/home/budget").get_data(as_text=True)
    footer = body.split('class="total-row')[1].split("</tr>")[0]
    cells = [
        re.sub(r"<[^>]+>", "", c).strip()
        for c in re.findall(r"<td[^>]*>(.*?)</td>", footer, flags=re.DOTALL)
    ]
    # This month: 4000 - 2000 - 15; nothing realized, so Delta is its negation.
    assert cells[0] == "Net"
    assert cells[5:8] == ["$1,985.00", "$0.00", "($1,985.00)"]


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
                "autoAccountRef": acct,
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


_MAY = "/s/home/budget?ym=2026-05"


def test_view_mode_shows_suggestion_count_linking_to_edit_mode(client, db_engine):
    _rent_with_unlinked_charge(db_engine)
    body = client.get(_MAY).get_data(as_text=True)
    badge = body.split("data-actuals-suggested")[1].split("</a>")[0]
    assert "1 suggested link" in badge
    assert "ym=2026-05" in badge and "edit=1" in badge
    assert "1 suggested" in _row(body, 0)
    # Actions are edit-mode only.
    assert "data-suggestion-apply" not in body


def test_edit_mode_lists_suggestion_under_entry(client, db_engine):
    _, _, txn_id = _rent_with_unlinked_charge(db_engine)
    body = client.get(_MAY + "&edit=1").get_data(as_text=True)
    detail = body.split('id="budget-row-0"')[1].split("</tr>", 1)[1]
    assert detail.lstrip().startswith(
        '<tr data-detail-row data-detail-of="budget-row-0" data-suggestions-for="0"'
    )
    assert f'data-transaction-id="{txn_id}"' in detail
    # It spans the full grid, actions column included.
    assert '<td colspan="12"' in detail and "table-actions-cell" in detail
    assert "Landlord" in detail
    assert "data-suggestion-apply" in detail
    assert "data-suggestion-dismiss" in detail
    # The actions post for the viewed month.
    assert "/budget/suggestion/link?ym=2026-05" in detail


def test_apply_links_and_rerenders_month(client, db_engine):
    _, ref, txn_id = _rent_with_unlinked_charge(db_engine)
    resp = client.post(
        "/s/home/budget/suggestion/link?ym=2026-05",
        data={"transaction_id": txn_id, "budget_entry_ref": ref},
        headers={"HX-Request": "true"},
    )
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "<html" not in body
    assert "Matched" in _row(body, 0)
    assert "data-suggestion-apply" not in body
    # The pager row's findings ride along out-of-band, now without a badge.
    assert 'id="budget-findings"' in body and 'hx-swap-oob="true"' in body
    assert "suggested link" not in body
    with db_engine.connect() as conn:
        assert get_correction(conn, txn_id)["budget_entry_ref"] == ref


def test_dismiss_hides_suggestion_without_linking(client, db_engine):
    _, ref, txn_id = _rent_with_unlinked_charge(db_engine)
    resp = client.post(
        "/s/home/budget/suggestion/dismiss?ym=2026-05",
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
    url = "/s/home/budget/suggestion/link"
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
        "/s/home/budget/suggestion/dismiss",
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
    row = _row(client.get(_MAY).get_data(as_text=True), 0)
    assert "data-actual-reversed" in row
    assert "Net of $200.00 in linked refunds" in row
    assert "($1,800.00)" in row


def test_drift_marks_the_actual(client, db_engine):
    """A linked charge at a different price than budgeted marks Actual with a
    drift dot and counts toward the pager row's drift badge."""
    sid, ref, txn_id = _rent_with_unlinked_charge(db_engine)
    with db_engine.connect() as conn:
        conn.execute(
            transactions.update()
            .where(transactions.c.id == txn_id)
            .values(amount="-2100.00")
        )
        conn.commit()
        link_transaction(conn, sid, txn_id, ref)
    body = client.get(_MAY).get_data(as_text=True)
    assert 'data-budget-mark="drift"' in _row(body, 0)
    assert "Price drift: +$100.00 per charge" in _row(body, 0)
    assert "1 drift" in body
