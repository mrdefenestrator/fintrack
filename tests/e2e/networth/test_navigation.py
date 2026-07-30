"""Navigation and page-loading e2e tests."""

import pytest

pytestmark = pytest.mark.e2e


def test_root_shows_file_selection(page, flask_server):
    """Root URL shows the file selection page with picker auto-opened."""
    page.goto(flask_server)
    assert page.url.rstrip("/") == flask_server.rstrip("/")
    assert page.locator("[data-file-picker-dropdown]").is_visible()


def test_nav_links(page, flask_server):
    """The sidebar navigates to each destination — one flat list across both
    groups, no domain toggle."""
    page.goto(f"{flask_server}/s/test_finances/holdings")

    page.click("[data-nav-sidebar] >> text=Budget")
    page.wait_for_url("**/budget**")
    assert "Budget" in page.title()

    page.click("[data-nav-sidebar] >> text=Projections")
    page.wait_for_url("**/projections**")
    assert "Projections" in page.title()

    # Spending destinations live in the same sidebar (cross-group is one click)
    page.click("[data-nav-sidebar] >> text=Trends")
    page.wait_for_url("**/trends**")
    assert "Trends" in page.title()

    page.click("[data-nav-sidebar] >> text=Holdings")
    page.wait_for_url("**/holdings**")
    assert "Holdings" in page.title()


def test_sidebar_marks_active_page(page, flask_server):
    """The current page is highlighted in the sidebar (aria-current)."""
    page.goto(f"{flask_server}/s/test_finances/holdings")
    active = page.locator("[data-nav-sidebar] a[aria-current='page']")
    assert active.inner_text().strip() == "Holdings"

    page.goto(f"{flask_server}/s/test_finances/budget")
    active = page.locator("[data-nav-sidebar] a[aria-current='page']")
    assert active.inner_text().strip() == "Budget"


def test_tab_row_height_stable_across_tabs(page, flask_server):
    """The single-row top bar is a constant height on every page — whether the
    route computes quick totals (holdings), doesn't (transactions), or belongs
    to neither nav group (import). Nav lives in the sidebar/drawer now, so the
    bar itself never grows or shrinks when switching pages."""
    heights = {}
    for path in ("holdings", "transactions", "import"):
        page.goto(f"{flask_server}/s/test_finances/{path}")
        box = page.locator("#main-header").bounding_box()
        assert box is not None, f"No header on /{path}"
        heights[path] = box["height"]

    assert heights["holdings"] == pytest.approx(heights["transactions"], abs=1)
    assert heights["holdings"] == pytest.approx(heights["import"], abs=1)


def test_import_icon_in_header(page, flask_server):
    """Import is a header icon button (not a tab) with an active state."""
    page.goto(f"{flask_server}/s/test_finances/holdings")

    # No Import tab in either nav row
    assert page.locator("nav >> text=Import").count() == 0

    icon = page.locator("[data-import-link]")
    assert icon.is_visible()
    assert icon.get_attribute("title").startswith("Import statements")
    assert icon.get_attribute("aria-current") is None

    icon.click()
    page.wait_for_url("**/import**")
    assert page.locator("[data-import-link]").get_attribute("aria-current") == "page"


def test_status_url_redirects_to_holdings(page, flask_server):
    """The removed status page's URL 302s to the Holdings view."""
    page.goto(f"{flask_server}/s/test_finances/status")
    page.wait_for_url("**/holdings**")
    assert "Holdings" in page.title()


def test_lock_scoped_to_edit_pages(page, flask_server):
    """The lock toggle is functional on holdings/budget and muted
    (non-interactive) on pages that don't honor edit mode."""
    for path in ("holdings", "budget", "merchants", "categories", "transactions"):
        page.goto(f"{flask_server}/s/test_finances/{path}")
        assert page.locator("button[title^='Enter edit mode']").is_visible(), path
        assert page.locator("[data-lock-muted]").count() == 0, path

    for path in ("projections", "import"):
        page.goto(f"{flask_server}/s/test_finances/{path}")
        assert page.locator("[data-lock-muted]").is_visible(), path
        assert page.locator("button[title^='Enter edit mode']").count() == 0, path
        assert page.locator("button[title^='Exit edit mode']").count() == 0, path


def test_global_edit_mode_toggle(page, flask_server):
    """Global lock/unlock button in header toggles edit mode across the
    finances tabs."""
    page.goto(f"{flask_server}/s/test_finances/holdings")

    # Initially locked: button shows 'Locked' (muted style)
    locked_btn = page.locator("button[title^='Enter edit mode']")
    assert locked_btn.is_visible()

    # Add row should not be visible when locked
    add_row = page.locator("[data-add-row]")
    assert add_row.count() == 0

    # Click to unlock
    locked_btn.click()
    page.wait_for_timeout(200)

    # Should now show 'Editing' (amber pill)
    editing_btn = page.locator("button[title^='Exit edit mode']")
    assert editing_btn.is_visible()

    # Add rows should appear in edit mode (Holdings has one per group)
    assert page.locator("[data-add-row]").first.is_visible()

    # Navigate to Budget via the sidebar — still in edit mode
    page.click("[data-nav-sidebar] >> text=Budget")
    page.wait_for_url("**/budget**")
    assert page.locator("button[title^='Exit edit mode']").is_visible()

    # Click to lock again
    page.locator("button[title^='Exit edit mode']").click()
    page.wait_for_timeout(200)
    assert page.locator("button[title^='Enter edit mode']").is_visible()
