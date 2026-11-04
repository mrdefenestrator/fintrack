"""Projections page e2e tests."""

import re

import pytest

pytestmark = pytest.mark.e2e


def test_projections_page_renders(page, flask_server):
    """Summary and grid render for the fixture snapshot; the old chart is gone."""
    page.goto(f"{flask_server}/s/test_finances/projections")
    assert "Projections" in page.title()
    assert page.locator("[data-projection-table]").is_visible()
    assert page.locator("[data-projection-chart]").count() == 0
    liquid = page.locator("[data-summary='liquid']")
    assert liquid.is_visible()
    assert "Liquid by" in liquid.inner_text()
    assert "now $5,000.00" in liquid.inner_text()
    assert page.locator("[data-summary='net-worth']").is_visible()
    assert "Lowest liquid" in page.locator("[data-summary='lowest']").inner_text()
    # "Now" column sits between Account and the first month.
    assert (
        page.locator("[data-projection-table] .sheet-group-header")
        .first.locator("th[data-sort-key='c1']")
        .inner_text()
        .strip()
        == "Now"
    )
    # Totals rows are present
    assert page.locator("[data-projection-table] >> text=Liquid total").is_visible()
    assert page.locator("[data-projection-table] >> text=Net worth").is_visible()


def test_projections_nav_tab(page, flask_server):
    page.goto(f"{flask_server}/s/test_finances/holdings")
    page.click("[data-nav-sidebar] >> text=Projections")
    page.wait_for_url("**/projections**")
    assert "Projections" in page.title()


def test_projections_grid_is_a_grouped_sheet(page, flask_server):
    """The accounts×months grid is a grouped sheet like Holdings: always-open
    bands with a sticky heading + column header each, and the Liquid / Net
    worth totals pinned in the footer."""
    page.goto(f"{flask_server}/s/test_finances/projections")
    tbl = page.locator("[data-projection-table]")
    assert tbl.is_visible()
    assert "grouped-sheet" in tbl.get_attribute("class")

    band = tbl.locator("tbody.sheet-group").first
    assert band.get_attribute("data-tone")
    heading = band.locator("tr.sheet-group-heading > td").first
    assert heading.evaluate("e => getComputedStyle(e).position") == "sticky"
    # Every data row is visible — bands no longer collapse.
    rows = tbl.locator("tr.sheet-group-row")
    assert rows.count() > 0
    for i in range(rows.count()):
        assert rows.nth(i).is_visible()

    assert tbl.locator("tr.total-row", has_text="Liquid total").count() == 1
    assert tbl.locator("tr.total-row", has_text="Net worth").count() == 1


def test_projections_sort_is_per_band(page, flask_server):
    """Sorting a band's column reorders only that band's rows; the totals stay
    in the footer."""
    page.goto(f"{flask_server}/s/test_finances/projections")
    tbl = page.locator("[data-projection-table]")
    band = tbl.locator("tbody.sheet-group").first
    band.locator(".sheet-group-header th[data-sort-key='c1']").click()
    assert band.get_attribute("data-sorted") == "1"
    assert band.locator("tr.total-row").count() == 0
    assert tbl.locator("tr.total-row").count() == 2


def test_projections_horizon_and_estimate_toggles(page, flask_server):
    page.goto(f"{flask_server}/s/test_finances/projections")

    # Target the horizon link by href — "24" as bare text is ambiguous.
    page.click("a[href*='months=24']")
    page.wait_for_url("**months=24**")
    assert page.locator("[data-projection-table]").is_visible()

    page.click("[data-estimate-toggle]")
    page.wait_for_url("**estimate=1**")
    assert page.locator("[data-projection-table]").is_visible()
    # months choice is preserved across the estimate toggle
    assert "months=24" in page.url
    # The estimate's breakdown is shown once the estimate is on.
    assert page.locator("[data-summary='estimate']").is_visible()


def test_projections_minimum_balance_warning(page, flask_server, _db_path):
    """An account that dips below its minimum gets one plain-language line."""
    from sqlalchemy import create_engine, update

    from fintrack.core.models import cash_details

    engine = create_engine(f"sqlite:///{_db_path}")
    with engine.begin() as conn:
        conn.execute(update(cash_details).values(minimum_balance=6000))
    engine.dispose()

    page.goto(f"{flask_server}/s/test_finances/projections")
    warnings = page.locator("[data-summary='warnings']")
    assert warnings.is_visible()
    assert "drops below its $6,000.00 minimum" in warnings.inner_text()


def test_projections_changes_view(page, flask_server):
    """The Changes toggle swaps balances for month-over-month changes and is
    kept when the horizon changes."""
    page.goto(f"{flask_server}/s/test_finances/projections")
    page.click("[data-view-toggle] >> text=Changes")
    page.wait_for_url("**view=change**")
    tbl = page.locator("[data-projection-table]")
    assert tbl.get_attribute("data-view") == "change"
    assert tbl.locator(".sheet-group-header", has_text="Now").count() == 0
    assert tbl.locator("tr.total-row", has_text="Liquid change").count() == 1
    assert tbl.locator("tr.total-row", has_text="Net worth change").count() == 1

    page.click("a[href*='months=6']")
    page.wait_for_url("**months=6**")
    assert "view=change" in page.url

    page.click("[data-view-toggle] >> text=Balances")
    page.wait_for_url(lambda url: "view=change" not in url)
    assert tbl.locator("tr.total-row", has_text="Liquid total").count() == 1


def test_projections_month_breakdown(page, flask_server):
    """The icon in a month's header opens that month's breakdown without
    sorting the column; arrows step through months; the close link hides it."""
    page.goto(f"{flask_server}/s/test_finances/projections")
    assert page.locator("[data-month-breakdown]").count() == 0

    tbl = page.locator("[data-projection-table]")
    second_month = tbl.locator(".sheet-group-header").first.locator(
        "th[data-sort-key='c3']"
    )
    label = second_month.inner_text().strip()
    second_month.locator("[data-header-link]").click()
    page.wait_for_url("**month=1**")

    panel = page.locator("[data-month-breakdown]")
    assert f"What happens in {label}" in panel.inner_text()
    # Salary lands on checking every month in the fixture.
    assert (
        panel.locator("[data-breakdown-group]", has_text="Main Checking").count() == 1
    )
    assert "Money in +$" in panel.locator("[data-breakdown-totals]").inner_text()
    # The header click didn't sort the column (sorting records sort_<group>_col).
    assert "sort_" not in page.url

    panel.locator("a[title='Next month']").click()
    page.wait_for_url("**month=2**")

    panel.locator("[data-breakdown-close]").click()
    page.wait_for_url(lambda url: not re.search(r"[?&]month=", url))
    assert page.locator("[data-month-breakdown]").count() == 0
