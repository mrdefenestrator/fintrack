"""Navigation and tab e2e tests for the ledger pages."""

import pytest

pytestmark = pytest.mark.e2e


def test_root_shows_snapshot_picker(page, flask_server):
    """Root URL shows the snapshot picker."""
    page.goto(flask_server)
    assert page.locator("[data-file-picker-dropdown]").is_visible()


def test_all_ledger_tabs_load(page, flask_server):
    """Each ledger page URL renders the header/nav and content area without
    errors."""
    tabs = ["/trends", "/transactions", "/merchants", "/import"]
    for path in tabs:
        page.goto(f"{flask_server}/s/ledger{path}")
        assert page.locator("#main-header").is_visible(), f"Header missing on {path}"
        assert page.locator("#content").is_visible(), f"Content missing on {path}"


def test_initial_active_tab_highlighted(page, flask_server):
    """The current ledger page is highlighted in the sidebar (aria-current)."""
    for path, name in (
        ("/transactions", "Transactions"),
        ("/trends", "Trends"),
        ("/merchants", "Merchants"),
    ):
        page.goto(f"{flask_server}/s/ledger{path}")
        active = page.locator("[data-nav-sidebar] a[aria-current='page']")
        assert active.inner_text().strip() == name, path


def test_import_page_no_active_sidebar_link(page, flask_server):
    """The import page activates the header import icon, not a sidebar link."""
    page.goto(f"{flask_server}/s/ledger/import")
    assert page.locator("[data-import-link]").get_attribute("aria-current") == "page"
    assert page.locator("[data-nav-sidebar] a[aria-current='page']").count() == 0


def test_tab_navigation(page, flask_server):
    """The sidebar navigates between ledger and net-worth pages; Import is a
    header icon."""
    page.goto(f"{flask_server}/s/ledger/trends")

    page.click("[data-nav-sidebar] >> text=Merchants")
    page.wait_for_url("**/merchants**")

    # Import is a header icon button, not a nav entry
    page.click("[data-import-link]")
    page.wait_for_url("**/import**")

    # Cross-group navigation is one click in the same sidebar
    page.click("[data-nav-sidebar] >> text=Holdings")
    page.wait_for_url("**/holdings**")


def test_merchants_page_renders_table(page, flask_server):
    """Merchants page renders the table structure even with no data."""
    page.goto(f"{flask_server}/s/ledger/merchants")
    assert page.locator("table").is_visible()
    assert page.locator("table thead").is_visible()


def test_import_page_renders_form(page, flask_server):
    """Import page renders the upload dropzone and the account panel.

    With no accounts in the snapshot the account panel shows the inline
    create-account form rather than a populated <select>.
    """
    page.goto(f"{flask_server}/s/ledger/import")
    assert page.locator("#dropzone").is_visible()
    assert page.locator("#account-panel").is_visible()
    # Empty snapshot → create form is shown instead of account dropdown
    assert page.locator("text=No accounts yet").is_visible()
