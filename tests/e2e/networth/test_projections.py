"""Projections page e2e tests."""

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
        page.locator("[data-projection-table] thead th[data-col='1']")
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


def test_projections_grid_sticky_chrome(page, flask_server):
    """The accounts×months grid adopts the shared sheet chrome: a sticky month
    header, a frozen first (Account) column pinned to the left edge, and the
    Liquid / Net worth totals pinned in a <tfoot> so client-side sort never
    reorders them."""
    page.goto(f"{flask_server}/s/test_finances/projections")
    tbl = page.locator("[data-projection-table]")
    assert tbl.is_visible()

    # Sticky month header (shared .table-scroll-container rule).
    assert (
        tbl.locator("thead").evaluate("e => getComputedStyle(e).position") == "sticky"
    )
    # Frozen first column: the corner header cell pins to the left edge.
    corner = tbl.locator("thead th:first-child")
    assert corner.evaluate("e => getComputedStyle(e).position") == "sticky"
    assert corner.evaluate("e => getComputedStyle(e).left") == "0px"

    # Both totals live in the <tfoot> as .total-row (pinned, out of the sort).
    assert tbl.locator("tfoot tr.total-row", has_text="Liquid total").count() == 1
    assert tbl.locator("tfoot tr.total-row", has_text="Net worth").count() == 1


def test_projections_sort_keeps_totals_in_footer(page, flask_server):
    """Sorting a column never pulls the summary rows out of the <tfoot> into
    the sortable <tbody>."""
    page.goto(f"{flask_server}/s/test_finances/projections")
    tbl = page.locator("[data-projection-table]")
    tbl.locator("thead th[data-col='1']").click()
    assert tbl.locator("tbody tr.total-row").count() == 0
    assert tbl.locator("tfoot tr.total-row").count() == 2
    assert tbl.locator("tfoot", has_text="Net worth").is_visible()


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
    assert tbl.locator("thead", has_text="Now").count() == 0
    assert tbl.locator("tfoot tr.total-row", has_text="Liquid change").count() == 1
    assert tbl.locator("tfoot tr.total-row", has_text="Net worth change").count() == 1

    page.click("a[href*='months=6']")
    page.wait_for_url("**months=6**")
    assert "view=change" in page.url

    page.click("[data-view-toggle] >> text=Balances")
    page.wait_for_url(lambda url: "view=change" not in url)
    assert tbl.locator("tfoot tr.total-row", has_text="Liquid total").count() == 1
