"""Navigation and tab e2e tests."""

import pytest

pytestmark = pytest.mark.e2e


def test_root_redirects_to_trends(page, flask_server):
    """Root URL redirects to /trends."""
    page.goto(flask_server)
    assert "/trends" in page.url
    assert page.locator("nav").is_visible()
    assert page.locator("#content").is_visible()


def test_all_tabs_load(page, flask_server):
    """Each tab URL renders the nav and content area without errors."""
    tabs = ["/trends", "/transactions", "/merchants", "/accounts", "/import"]
    for path in tabs:
        page.goto(f"{flask_server}{path}")
        assert page.locator("nav").is_visible(), f"Nav missing on {path}"
        assert page.locator("#content").is_visible(), f"Content missing on {path}"


def test_initial_active_tab_highlighted(page, flask_server):
    """Server-rendered active tab has the blue highlight classes on initial load."""
    for path in ("/trends", "/merchants", "/import"):
        page.goto(f"{flask_server}{path}")
        active = page.locator(f"nav a[href='{path}']")
        classes = active.get_attribute("class")
        assert "text-blue-600" in classes, f"Tab not highlighted on {path}"
        assert "border-blue-500" in classes, f"Tab border not highlighted on {path}"


def test_htmx_tab_navigation(page, flask_server):
    """Clicking a tab swaps content via HTMX and updates the URL."""
    page.goto(f"{flask_server}/trends")

    page.click("nav a[href='/merchants']")
    page.wait_for_url("**/merchants**")

    page.click("nav a[href='/import']")
    page.wait_for_url("**/import**")

    page.click("nav a[href='/transactions']")
    page.wait_for_url("**/transactions**")


def test_active_tab_updates_after_htmx_navigation(page, flask_server):
    """Active tab indicator updates via JS after HTMX tab switch."""
    page.goto(f"{flask_server}/trends")

    page.click("nav a[href='/merchants']")
    page.wait_for_url("**/merchants**")
    # updateActiveTab() fires on htmx:afterSettle
    page.wait_for_function(
        "document.querySelector(\"nav a[href='/merchants']\").classList.contains('text-blue-600')"
    )

    trends_tab = page.locator("nav a[href='/trends']")
    assert "text-blue-600" not in trends_tab.get_attribute("class")


def test_merchants_page_renders_table(page, flask_server):
    """Merchants page renders the table structure even with no data."""
    page.goto(f"{flask_server}/merchants")
    assert page.locator("table").is_visible()
    assert page.locator("table thead").is_visible()


def test_import_page_renders_form(page, flask_server):
    """Import page renders the upload dropzone and the account panel.

    With an empty database the account panel shows the inline
    create-account form rather than a populated <select>.
    """
    page.goto(f"{flask_server}/import")
    assert page.locator("#dropzone").is_visible()
    assert page.locator("#account-panel").is_visible()
    # Empty DB → create form is shown instead of account dropdown
    assert page.locator("text=No accounts yet").is_visible()
