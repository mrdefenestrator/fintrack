"""Navigation and tab e2e tests for the ledger pages."""

import pytest

pytestmark = pytest.mark.e2e


def test_root_shows_snapshot_picker(page, flask_server):
    """Root URL shows the snapshot picker."""
    page.goto(flask_server)
    assert page.locator("[data-file-picker-dropdown]").is_visible()


def test_all_ledger_tabs_load(page, flask_server):
    """Each ledger tab URL renders the nav and content area without errors."""
    tabs = ["/trends", "/transactions", "/merchants", "/import"]
    for path in tabs:
        page.goto(f"{flask_server}/s/ledger{path}")
        assert page.locator("nav").is_visible(), f"Nav missing on {path}"
        assert page.locator("#content").is_visible(), f"Content missing on {path}"


def test_initial_active_tab_highlighted(page, flask_server):
    """Server-rendered active tab has the highlight classes on initial load."""
    for path in ("/trends", "/merchants", "/import"):
        page.goto(f"{flask_server}/s/ledger{path}")
        active = page.locator(f"nav a[href='/s/ledger{path}']")
        classes = active.get_attribute("class")
        assert "border-white" in classes, f"Tab not highlighted on {path}"


def test_tab_navigation(page, flask_server):
    """Clicking nav tabs navigates between ledger and net-worth pages."""
    page.goto(f"{flask_server}/s/ledger/trends")

    page.click("nav a[href='/s/ledger/merchants']")
    page.wait_for_url("**/merchants**")

    page.click("nav a[href='/s/ledger/import']")
    page.wait_for_url("**/import**")

    page.click("nav >> text=Accounts")
    page.wait_for_url("**/accounts**")

    page.click("nav a[href='/s/ledger/transactions']")
    page.wait_for_url("**/transactions**")
    active = page.locator("nav a[href='/s/ledger/transactions']")
    assert "border-white" in active.get_attribute("class")


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
