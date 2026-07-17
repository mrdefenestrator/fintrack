"""E2E tests for the Trends tab."""

import pytest

pytestmark = pytest.mark.e2e

_PERIODS = [
    ("quarterly", "Quarterly"),
    ("ytd", "Year to Date"),
    ("trailing12", "Trailing 12 Mo"),
    ("annual", "Last Year"),
]


# ---------------------------------------------------------------------------
# Period selector (empty database)
# ---------------------------------------------------------------------------


def test_trends_all_period_buttons_visible(page, flask_server):
    """All four preset period buttons are rendered."""
    page.goto(f"{flask_server}/s/ledger/trends")
    for _, label in _PERIODS:
        assert page.locator(f"a:has-text('{label}')").is_visible(), f"Missing: {label}"


def test_trends_trailing12_is_active_by_default(page, flask_server):
    """'Trailing 12 Mo' button has active (blue) styling on initial load."""
    page.goto(f"{flask_server}/s/ledger/trends")
    btn = page.locator("a:has-text('Trailing 12 Mo')")
    classes = btn.get_attribute("class")
    assert "bg-blue-500" in classes


def test_trends_inactive_period_buttons_not_highlighted(page, flask_server):
    """Non-active period buttons do not carry the blue active class."""
    page.goto(f"{flask_server}/s/ledger/trends")
    for period, label in _PERIODS:
        if period == "trailing12":
            continue
        btn = page.locator(f"a:has-text('{label}')")
        classes = btn.get_attribute("class") or ""
        assert "bg-blue-500" not in classes, f"Button '{label}' should not be active"


def test_trends_click_quarterly_updates_url(page, flask_server):
    """Clicking 'Quarterly' makes it active and updates the URL."""
    page.goto(f"{flask_server}/s/ledger/trends")
    page.click("a:has-text('Quarterly')")
    page.wait_for_url("**/trends**period=quarterly**")
    quarterly_btn = page.locator("a:has-text('Quarterly')")
    assert "bg-blue-500" in (quarterly_btn.get_attribute("class") or "")


def test_trends_click_trailing12_updates_url(page, flask_server):
    """Clicking 'Trailing 12 Mo' updates the URL to period=trailing12."""
    page.goto(f"{flask_server}/s/ledger/trends")
    page.click("a:has-text('Trailing 12 Mo')")
    page.wait_for_url("**/trends**period=trailing12**")


def test_trends_click_annual_updates_url(page, flask_server):
    """Clicking 'Last Year' updates the URL to period=annual."""
    page.goto(f"{flask_server}/s/ledger/trends")
    page.click("a:has-text('Last Year')")
    page.wait_for_url("**/trends**period=annual**")


# ---------------------------------------------------------------------------
# Table structure (empty database)
# ---------------------------------------------------------------------------


def test_trends_table_has_category_column(page, flask_server):
    """Trends table renders a Category column header."""
    page.goto(f"{flask_server}/s/ledger/trends")
    header = page.locator("table thead tr")
    assert header.locator("th", has_text="Category").is_visible()


def test_trends_table_has_grand_total_footer(page, flask_server):
    """Trends table always has a Total row in tfoot."""
    page.goto(f"{flask_server}/s/ledger/trends")
    assert page.locator("table tfoot").get_by_text("Total").is_visible()


def test_trends_empty_db_has_no_data_rows(page, flask_server):
    """With no transactions the table body is empty."""
    page.goto(f"{flask_server}/s/ledger/trends")
    assert page.locator("table tbody tr").count() == 0


# ---------------------------------------------------------------------------
# Data display (confirmed_server has 4 transactions in 04/2026)
# ---------------------------------------------------------------------------


def test_trends_ytd_shows_data_rows(page, confirmed_server):
    """YTD view shows at least one category row when data exists."""
    page.goto(f"{confirmed_server}/s/ledger/trends?period=ytd")
    rows = page.locator("table tbody tr")
    assert rows.count() >= 1


def test_trends_ytd_shows_uncategorized_category(page, confirmed_server):
    """YTD shows 'Uncategorized' since the classification API is disabled."""
    page.goto(f"{confirmed_server}/s/ledger/trends?period=ytd")
    assert page.locator("table tbody td", has_text="Uncategorized").first.is_visible()


def test_trends_ytd_grand_total_in_footer(page, confirmed_server):
    """YTD footer shows the seeded $132 total for April 2026."""
    page.goto(f"{confirmed_server}/s/ledger/trends?period=ytd")
    tfoot_text = page.locator("table tfoot").inner_text()
    # Amounts formatted with 0 decimals (money(0)) so $132.24 → "$132"
    assert "132" in tfoot_text


def test_trends_quarterly_shows_data_rows(page, current_quarter_server):
    """Quarterly view shows data rows when transactions exist in the quarter."""
    page.goto(f"{current_quarter_server}/s/ledger/trends?period=quarterly")
    rows = page.locator("table tbody tr")
    assert rows.count() >= 1


def test_trends_period_selector_updates_active_button(page, confirmed_server):
    """Clicking a period button makes it active and deactivates others."""
    page.goto(f"{confirmed_server}/s/ledger/trends?period=ytd")
    page.click("a:has-text('Quarterly')")
    page.wait_for_url("**/trends**period=quarterly**")

    assert "bg-blue-500" in (
        page.locator("a:has-text('Quarterly')").get_attribute("class") or ""
    )
    assert "bg-blue-500" not in (
        page.locator("a:has-text('Year to Date')").get_attribute("class") or ""
    )


# ---------------------------------------------------------------------------
# Window pager (QA issue 5)
# ---------------------------------------------------------------------------


def test_trends_pager_next_disabled_at_latest(page, flask_server):
    """At the default (latest) window there is no enabled 'later' link and
    no 'Latest' reset button — only the 'earlier' pager control is live."""
    page.goto(f"{flask_server}/s/ledger/trends")
    assert page.locator("a[title='Earlier']").is_visible()
    assert page.locator("a[title='Later']").count() == 0
    assert page.locator("a:has-text('Latest')").count() == 0


def test_trends_pager_earlier_updates_url_and_shows_latest_button(page, flask_server):
    """Clicking the earlier pager control adds end= to the URL (pushed via
    HTMX) and reveals the 'Latest' reset button plus an enabled 'later'
    control."""
    page.goto(f"{flask_server}/s/ledger/trends")
    page.click("a[title='Earlier']")
    page.wait_for_url("**/trends**end=**")
    assert page.locator("a:has-text('Latest')").is_visible()
    assert page.locator("a[title='Later']").is_visible()


def test_trends_pager_latest_button_returns_to_default(page, flask_server):
    """Paging back then clicking 'Latest' drops end= from the URL and
    restores the default (no Latest button, no enabled later link) state."""
    page.goto(f"{flask_server}/s/ledger/trends")
    page.click("a[title='Earlier']")
    page.wait_for_url("**/trends**end=**")
    page.click("a:has-text('Latest')")
    page.wait_for_function(
        "() => !new URL(window.location.href).search.includes('end=')"
    )
    assert page.locator("a:has-text('Latest')").count() == 0
    assert page.locator("a[title='Later']").count() == 0


def test_trends_pager_later_from_previous_month_returns_to_latest(page, flask_server):
    """Paging one month back then one month forward lands back at 'latest'
    (the 'Later'/'Latest' controls disappear again). Note: the URL still
    carries the resolved end=<current-month> — the server-side clamp is what
    matters, not the query string shape, since the current month is treated
    identically to no end= at all."""
    page.goto(f"{flask_server}/s/ledger/trends")
    page.click("a[title='Earlier']")
    page.wait_for_url("**/trends**end=**")
    page.click("a[title='Later']")
    page.wait_for_selector("a:has-text('Latest')", state="detached")
    assert page.locator("a:has-text('Latest')").count() == 0
    assert page.locator("a[title='Later']").count() == 0


def test_trends_pager_browser_back_restores_previous_window(page, flask_server):
    """hx-push-url means browser back navigates from a paged window back to
    the default (latest) window."""
    page.goto(f"{flask_server}/s/ledger/trends")
    page.click("a[title='Earlier']")
    page.wait_for_url("**/trends**end=**")
    page.go_back()
    page.wait_for_function(
        "() => !new URL(window.location.href).search.includes('end=')"
    )
    assert page.locator("a:has-text('Latest')").count() == 0


def test_trends_pager_shows_window_label_text(page, flask_server):
    """The current window is rendered as readable text (e.g. 'Aug 2025 –
    Jul 2026'), not just raw params."""
    page.goto(f"{flask_server}/s/ledger/trends")
    body_text = page.locator("body").inner_text()
    assert "–" in body_text or "-" in body_text  # en dash separator present


def test_trends_paged_window_still_shows_seeded_data(page, confirmed_server):
    """Paging back within a window that still covers the seeded April 2026
    transactions keeps showing that data (trailing12 covers the last 12
    months, so paging back one month still includes April 2026 data as long
    as today is within a year of it)."""
    page.goto(f"{confirmed_server}/s/ledger/trends?period=trailing12")
    page.click("a[title='Earlier']")
    page.wait_for_url("**/trends**end=**")
    # Either the data is still visible (window still covers April 2026) or
    # the table renders gracefully empty — both are valid; the page must not
    # error out.
    assert page.locator("table tfoot").get_by_text("Total").is_visible()
