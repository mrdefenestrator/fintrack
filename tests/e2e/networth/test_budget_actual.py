"""Budget vs Actual page e2e tests (issue #73) — pager, filters, sort, total row.

Pinned to April 2026, when the fixture's monthly entries (Salary, Freelance,
Rent, Groceries) are all active and nothing is linked.
"""

from datetime import datetime

import pytest

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
