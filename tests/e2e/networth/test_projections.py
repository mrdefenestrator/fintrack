"""Projections page e2e tests."""

import pytest

pytestmark = pytest.mark.e2e


def test_projections_page_renders(page, flask_server):
    """Grid and chart render for the fixture snapshot."""
    page.goto(f"{flask_server}/s/test_finances/projections")
    assert "Projections" in page.title()
    assert page.locator("[data-projection-table]").is_visible()
    assert page.locator("[data-projection-chart]").is_visible()
    # Totals rows are present
    assert page.locator("[data-projection-table] >> text=Liquid total").is_visible()
    assert page.locator("[data-projection-table] >> text=Net worth").is_visible()


def test_projections_nav_tab(page, flask_server):
    page.goto(f"{flask_server}/s/test_finances/holdings")
    page.click("[data-nav-sub] >> text=Projections")
    page.wait_for_url("**/projections**")
    assert "Projections" in page.title()


def test_projections_horizon_and_estimate_toggles(page, flask_server):
    page.goto(f"{flask_server}/s/test_finances/projections")

    # Target the horizon link by href — "24" as bare text now also appears in
    # the chart's axis labels / tooltips, so text=24 is ambiguous.
    page.click("a[href*='months=24']")
    page.wait_for_url("**months=24**")
    assert page.locator("[data-projection-table]").is_visible()

    page.click("[data-estimate-toggle]")
    page.wait_for_url("**estimate=1**")
    assert page.locator("[data-projection-table]").is_visible()
    # months choice is preserved across the estimate toggle
    assert "months=24" in page.url
