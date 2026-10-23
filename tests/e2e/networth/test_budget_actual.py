"""Budget vs Actual page e2e tests (issue #73) — pager, filters, sort, total row.

Pinned to April 2026, when the fixture's monthly entries (Salary, Freelance,
Rent, Groceries) are all active and nothing is linked.
"""

import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, insert, select

from fintrack.core.models import holdings, imports, snapshots, transactions
from tests.e2e.networth.conftest import enable_edit_mode

pytestmark = pytest.mark.e2e

_URL = "/s/test_finances/budget/actual"


def _rows(page):
    return page.locator("#budget-actual-table tbody tr:not(.total-row)")


def test_budget_actual_lists_entries_for_month(page, flask_server):
    page.goto(f"{flask_server}{_URL}?year=2026&month=4")
    assert page.locator("text=Apr 2026").first.is_visible()
    table = page.locator("#budget-actual-table")
    assert table.get_by_text("Salary", exact=True).is_visible()
    assert table.get_by_text("Rent", exact=True).is_visible()


def test_budget_actual_prev_arrow_navigates(page, flask_server):
    page.goto(f"{flask_server}{_URL}?year=2026&month=4")
    page.click("a[title='Earlier']")
    page.wait_for_url("**/budget/actual?year=2026&month=3**")
    assert page.locator("text=Mar 2026").first.is_visible()


def test_budget_actual_next_arrow_navigates(page, flask_server):
    page.goto(f"{flask_server}{_URL}?year=2026&month=4")
    page.click("a[title='Later']")
    page.wait_for_url("**/budget/actual?year=2026&month=5**")


def test_budget_actual_latest_jumps_to_current_month(page, flask_server):
    page.goto(f"{flask_server}{_URL}?year=2020&month=1")
    page.click("a:has-text('Latest')")
    today = datetime.now().astimezone().date()
    page.wait_for_url(f"**/budget/actual?year={today.year}&month={today.month}**")


def test_budget_actual_kind_filter_swaps_rows(page, flask_server):
    page.goto(f"{flask_server}{_URL}?year=2026&month=4")
    page.click("button.filter-dropdown-trigger:has-text('Kind')")
    page.check("input[name=kind][value=expense]")
    page.wait_for_url("**kind=expense**")
    table = page.locator("#budget-actual-table")
    assert table.get_by_text("Rent", exact=True).is_visible()
    assert table.get_by_text("Salary", exact=True).count() == 0


def test_budget_actual_filter_survives_paging(page, flask_server):
    page.goto(f"{flask_server}{_URL}?year=2026&month=4&kind=expense")
    page.click("a[title='Earlier']")
    page.wait_for_url("**month=3**")
    assert "kind=expense" in page.url
    assert page.locator("#budget-actual-table").get_by_text("Salary").count() == 0


def test_budget_actual_total_row_stays_last_when_sorted(page, flask_server):
    page.goto(f"{flask_server}{_URL}?year=2026&month=4")
    assert _rows(page).count() >= 2
    page.locator("#budget-actual-table th[data-col='2']").click()  # Expected
    last_row = page.locator("#budget-actual-table tbody tr").last
    assert "total-row" in (last_row.get_attribute("class") or "")
    assert "net" in last_row.inner_text()


# --- Link suggestions (issue #70) -------------------------------------------


def _add_rent_charge(db_path) -> None:
    """An unlinked April rent charge on Main Checking, matching the fixture's
    $1500 Rent entry exactly, so it's suggested with high confidence."""
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.connect() as conn:
        acct = conn.execute(
            select(holdings.c.id)
            .join(snapshots, holdings.c.snapshot_id == snapshots.c.id)
            .where(
                snapshots.c.name == "test_finances",
                holdings.c.name == "Main Checking",
            )
        ).scalar_one()
        imp = conn.execute(
            insert(imports).values(
                account_id=acct,
                holding_group="cash",
                filename="e2e.ofx",
                file_hash=uuid.uuid4().hex,
                status="confirmed",
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(transactions).values(
                import_id=imp,
                account_id=acct,
                date=date(2026, 4, 1),
                amount="-1500.00",
                raw_description="LANDLORD LLC",
                normalized_merchant="Landlord",
                fingerprint=uuid.uuid4().hex,
            )
        )
        conn.commit()
    engine.dispose()


def _rent_row(page):
    return _rows(page).filter(has=page.get_by_text("Rent", exact=True)).first


def test_suggestion_count_links_to_edit_mode(page, flask_server, _db_path):
    _add_rent_charge(_db_path)
    page.goto(f"{flask_server}{_URL}?year=2026&month=4")
    assert page.locator("[data-suggestion-apply]").count() == 0
    page.click("a[data-actuals-suggested]")
    page.wait_for_url("**edit=1**")
    assert page.locator("[data-suggestion]").get_by_text("Landlord").is_visible()


def test_apply_suggestion_links_transaction(page, flask_server, _db_path):
    _add_rent_charge(_db_path)
    page.goto(f"{flask_server}{_URL}?year=2026&month=4")
    enable_edit_mode(page)
    page.click("[data-suggestion-apply]")
    page.locator("[data-suggestion]").wait_for(state="detached")
    assert _rent_row(page).get_by_text("Matched").is_visible()
    assert "month=4" in page.url


def test_dismissed_suggestion_stays_dismissed(page, flask_server, _db_path):
    _add_rent_charge(_db_path)
    page.goto(f"{flask_server}{_URL}?year=2026&month=4&edit=1")
    page.click("[data-suggestion-dismiss]")
    page.locator("[data-suggestion]").wait_for(state="detached")
    page.reload()
    assert page.locator("[data-actuals-suggested]").count() == 0
    assert _rent_row(page).get_by_text("Not linked").is_visible()
