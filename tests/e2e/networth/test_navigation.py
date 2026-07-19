"""Navigation and page-loading e2e tests."""

import pytest

pytestmark = pytest.mark.e2e


def test_root_shows_file_selection(page, flask_server):
    """Root URL shows the file selection page with picker auto-opened."""
    page.goto(flask_server)
    assert page.url.rstrip("/") == flask_server.rstrip("/")
    assert page.locator("[data-file-picker-dropdown]").is_visible()


def test_nav_links(page, flask_server):
    """Finances sub-tab links navigate to each page."""
    page.goto(f"{flask_server}/s/test_finances/accounts")

    page.click("[data-nav-sub] >> text=Budget")
    page.wait_for_url("**/budget**")
    assert "Budget" in page.title()

    page.click("[data-nav-sub] >> text=Assets")
    page.wait_for_url("**/assets**")
    assert "Assets" in page.title()

    page.click("[data-nav-sub] >> text=Accounts")
    page.wait_for_url("**/accounts**")
    assert "Accounts" in page.title()

    page.click("[data-nav-sub] >> text=Projections")
    page.wait_for_url("**/projections**")
    assert "Projections" in page.title()


def test_group_tabs_navigate_to_landing_pages(page, flask_server):
    """Group tabs land on the group's default page and get the active marker."""
    page.goto(f"{flask_server}/s/test_finances/accounts")
    finances = page.locator("[data-nav-group='finances']")
    assert "border-white" in finances.get_attribute("class").split()

    page.click("[data-nav-group='spending']")
    page.wait_for_url("**/transactions**")
    spending = page.locator("[data-nav-group='spending']")
    assert "border-white" in spending.get_attribute("class").split()
    assert (
        "border-white"
        not in page.locator("[data-nav-group='finances']")
        .get_attribute("class")
        .split()
    )

    page.click("[data-nav-group='finances']")
    page.wait_for_url("**/accounts**")


def test_group_tab_remembers_last_subtab(page, flask_server):
    """The group tab links back to the last-visited sub-tab (sessionStorage)."""
    page.goto(f"{flask_server}/s/test_finances/budget")

    page.click("[data-nav-group='spending']")
    page.wait_for_url("**/transactions**")

    # Finances tab now points back at Budget, not the Accounts default
    page.click("[data-nav-group='finances']")
    page.wait_for_url("**/budget**")


def test_tab_row_height_stable_across_tabs(page, flask_server):
    """Header (title/group-tab row + sub-tab row) must be the same height whether the
    current route computes quick totals (accounts), doesn't (transactions),
    or belongs to neither group (import) — regression test for the tab-row
    vertical jitter (QA item 16). Every tab always renders a totals line
    (visible or an invisible placeholder), and the sub-tab row is always
    rendered (with an invisible placeholder tab on import), so the header
    can no longer grow/shrink when switching pages."""
    heights = {}
    for path in ("accounts", "transactions", "import"):
        page.goto(f"{flask_server}/s/test_finances/{path}")
        box = page.locator("#main-header").bounding_box()
        assert box is not None, f"No header on /{path}"
        heights[path] = box["height"]

    assert heights["accounts"] == pytest.approx(heights["transactions"], abs=1)
    assert heights["accounts"] == pytest.approx(heights["import"], abs=1)


def test_import_icon_in_header(page, flask_server):
    """Import is a header icon button (not a tab) with an active state."""
    page.goto(f"{flask_server}/s/test_finances/accounts")

    # No Import tab in either nav row
    assert page.locator("nav >> text=Import").count() == 0

    icon = page.locator("[data-import-link]")
    assert icon.is_visible()
    assert icon.get_attribute("title") == "Import statements"
    assert icon.get_attribute("aria-current") is None

    icon.click()
    page.wait_for_url("**/import**")
    assert page.locator("[data-import-link]").get_attribute("aria-current") == "page"


def test_status_url_redirects_to_accounts(page, flask_server):
    """The removed status page's URL 302s to the accounts view."""
    page.goto(f"{flask_server}/s/test_finances/status")
    page.wait_for_url("**/accounts**")
    assert "Accounts" in page.title()


def test_lock_scoped_to_edit_pages(page, flask_server):
    """The lock toggle is functional on accounts/budget/assets and muted
    (non-interactive) on pages that don't honor edit mode."""
    for path in ("accounts", "budget", "assets"):
        page.goto(f"{flask_server}/s/test_finances/{path}")
        assert page.locator("button[title='Enter edit mode']").is_visible(), path
        assert page.locator("[data-lock-muted]").count() == 0, path

    for path in ("transactions", "projections", "import"):
        page.goto(f"{flask_server}/s/test_finances/{path}")
        assert page.locator("[data-lock-muted]").is_visible(), path
        assert page.locator("button[title='Enter edit mode']").count() == 0, path
        assert page.locator("button[title='Exit edit mode']").count() == 0, path


def test_global_edit_mode_toggle(page, flask_server):
    """Global lock/unlock button in header toggles edit mode across the
    finances tabs."""
    page.goto(f"{flask_server}/s/test_finances/accounts")

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
    page.click("[data-nav-sub] >> text=Budget")
    page.wait_for_url("**/budget**")
    assert page.locator("button[title='Exit edit mode']").is_visible()

    # Click to lock again
    page.locator("button[title='Exit edit mode']").click()
    page.wait_for_timeout(200)
    assert page.locator("button[title='Enter edit mode']").is_visible()
