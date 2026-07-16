"""Navigation and page-loading e2e tests."""

import pytest

pytestmark = pytest.mark.e2e


def test_root_shows_file_selection(page, flask_server):
    """Root URL shows the file selection page with picker auto-opened."""
    page.goto(flask_server)
    assert page.url.rstrip("/") == flask_server.rstrip("/")
    assert page.locator("[data-file-picker-dropdown]").is_visible()


def test_nav_links(page, flask_server):
    """Nav bar links navigate to each page."""
    page.goto(f"{flask_server}/f/test_finances/accounts")

    page.click("nav >> text=Accounts")
    page.wait_for_url("**/accounts**")
    assert "Accounts" in page.title()

    page.click("nav >> text=Budget")
    page.wait_for_url("**/budget**")
    assert "Budget" in page.title()

    page.click("nav >> text=Assets")
    page.wait_for_url("**/assets**")
    assert "Assets" in page.title()


def test_global_edit_mode_toggle(page, flask_server):
    """Global lock/unlock button in header toggles edit mode across tabs."""
    page.goto(f"{flask_server}/f/test_finances/accounts")

    # Initially locked: button shows 'Locked' (muted style)
    locked_btn = page.locator("button[title='Enter edit mode']")
    assert locked_btn.is_visible()

    # Add row should not be visible when locked
    add_row = page.locator("[data-add-row]")
    assert add_row.count() == 0

    # Click to unlock
    locked_btn.click()
    page.wait_for_timeout(200)

    # Should now show 'Editing' (amber pill)
    editing_btn = page.locator("button[title='Exit edit mode']")
    assert editing_btn.is_visible()

    # Add row should appear in edit mode
    assert page.locator("[data-add-row]").is_visible()

    # Navigate to Budget — still in edit mode
    page.click("nav >> text=Budget")
    page.wait_for_url("**/budget**")
    assert page.locator("button[title='Exit edit mode']").is_visible()

    # Click to lock again
    page.locator("button[title='Exit edit mode']").click()
    page.wait_for_timeout(200)
    assert page.locator("button[title='Enter edit mode']").is_visible()
