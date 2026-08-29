"""E2E tests for the Merchants tab.

The Merchants list uses the same spreadsheet-style inline editing as the
accounts/budget/assets sheets: click the Category cell → it becomes a <select>
that auto-saves on change (no far-right edit button, no far-left Save button).

Row selectors deliberately exclude `.sheet-grid-filler` — the aria-hidden
spacer row that sheet-scroll.js injects to paint grid lines over empty space.
It is a real <tbody><tr>, so a bare `table tbody tr` counts it as a data row;
on an empty sheet that makes the count 1 (never 0), which broke the seed-on-
empty fallback and made these tests flaky depending on whether the filler had
been injected yet.
"""

import pytest

pytestmark = pytest.mark.e2e


def _open_inline_editor(page, cell):
    """Click a display cell to open its inline editor, blocking until htmx has
    both swapped in AND settled the new content.

    The inline editors save on the swapped-in control's own hx-trigger (a
    <select>'s `change`, an <input>'s `focusout`/Enter). htmx wires those
    triggers while processing the swap, which happens *after* the /cell
    response is received. So `expect_response('/cell')` returning does NOT mean
    the editor is interactive — selecting/typing in that gap fires a change
    event that htmx isn't listening for yet, the POST never goes out, and the
    test times out. This was the source of the flaky merchants tests.

    Waiting for htmx's `afterSettle` event (fired only after the swap is
    processed and triggers are wired) makes opening the editor deterministic,
    regardless of machine speed. Uses a document-level settle counter so a
    swap that completes between arming and the wait is still observed.
    """
    page.evaluate(
        "() => { if (!window.__hxSettleHooked) { window.__hxSettleHooked = true;"
        " window.__hxSettleCount = 0;"
        " document.addEventListener('htmx:afterSettle',"
        " () => { window.__hxSettleCount++; }); } }"
    )
    before = page.evaluate("() => window.__hxSettleCount || 0")
    cell.click()
    page.wait_for_function("(b) => (window.__hxSettleCount || 0) > b", arg=before)


# ---------------------------------------------------------------------------
# Filter controls and table structure (empty database)
# ---------------------------------------------------------------------------


def test_merchants_search_input_present(page, flask_server):
    """Merchant search input is rendered."""
    page.goto(f"{flask_server}/s/ledger/merchants")
    assert page.locator("input[name='search']").is_visible()


def test_merchants_category_filter_present(page, flask_server):
    """Category dropdown filter is rendered."""
    page.goto(f"{flask_server}/s/ledger/merchants")
    assert page.locator(
        "button.filter-dropdown-trigger:has-text('Category')"
    ).is_visible()


def test_merchants_source_filter_present(page, flask_server):
    """Source dropdown filter is rendered with Auto and Manual options."""
    page.goto(f"{flask_server}/s/ledger/merchants")
    source_trigger = page.locator("button.filter-dropdown-trigger:has-text('Source')")
    assert source_trigger.is_visible()
    source_trigger.click()
    labels = page.locator("input[name='source']").locator("xpath=..").all_inner_texts()
    assert any("Auto" in t for t in labels)
    assert any("Manual" in t for t in labels)


def test_merchants_table_has_expected_columns(page, flask_server):
    """Merchants table renders all expected header columns."""
    page.goto(f"{flask_server}/s/ledger/merchants")
    header = page.locator("table thead tr")
    for col in ("Merchant", "Category", "Source", "Transactions", "Last Seen"):
        assert header.locator("th", has_text=col).is_visible(), f"Missing column: {col}"


def test_merchants_empty_db_has_no_data_rows(page, flask_server):
    """With no merchant cache entries the table body is empty."""
    page.goto(f"{flask_server}/s/ledger/merchants")
    assert page.locator("table tbody tr:not(.sheet-grid-filler)").count() == 0


def test_merchants_uses_sheet_style_sortable_table(page, flask_server):
    """The table adopts the Finances sheet: a scroll container + sortable headers."""
    page.goto(f"{flask_server}/s/ledger/merchants")
    assert page.locator("[data-sheet-scroll] table.sortable").count() == 1
    for col in range(5):
        assert page.locator(f"th.sortable-th[data-col='{col}']").count() == 1


# ---------------------------------------------------------------------------
# Data display and inline editing (confirmed_server)
# ---------------------------------------------------------------------------


def test_merchants_empty_after_import_before_categorization(page, confirmed_server):
    """Merchants table is empty right after import since the API is disabled."""
    page.goto(f"{confirmed_server}/s/ledger/merchants")
    assert page.locator("table tbody tr:not(.sheet-grid-filler)").count() == 0


def _seed_merchant_via_transaction(page, base_url, category="Groceries"):
    """Populate the merchant cache by categorizing a transaction with the
    inline Category editor's apply-to-merchant option checked."""
    page.goto(f"{base_url}/s/ledger/transactions?year=2026&month=4&edit=1")
    page.wait_for_selector("table tbody tr:not(.sheet-grid-filler)")

    # Open the Category cell (5th column) on the first row, waiting until htmx
    # has wired the swapped-in editor (see _open_inline_editor).
    first_row = page.locator("table tbody tr:not(.sheet-grid-filler)").first
    _open_inline_editor(page, first_row.locator("td").nth(4))

    edit_row = page.locator("table tbody tr:not(.sheet-grid-filler)").first
    # apply-to-merchant is checked by default; selecting a category saves and
    # (because it's merchant-wide) redirects/reloads the list.
    edit_row.locator("input[name='apply_to_merchant']").check()
    with page.expect_response(lambda r: r.request.method == "POST"):
        edit_row.locator("select[name='value']").select_option(category)
    page.wait_for_load_state("networkidle")


def test_merchants_appear_after_transaction_correction(page, confirmed_server):
    """Categorizing a transaction merchant-wide populates the Merchants tab."""
    _seed_merchant_via_transaction(page, confirmed_server)

    page.goto(f"{confirmed_server}/s/ledger/merchants")
    rows = page.locator("table tbody tr:not(.sheet-grid-filler)")
    assert rows.count() >= 1
    row_text = rows.first.inner_text()
    assert "Groceries" in row_text
    assert "manual" in row_text.lower()


def test_merchants_category_cell_opens_inline_select(page, confirmed_server):
    """Clicking a merchant's Category cell swaps it to an inline <select>."""
    page.goto(f"{confirmed_server}/s/ledger/merchants?edit=1")
    if page.locator("table tbody tr:not(.sheet-grid-filler)").count() == 0:
        _seed_merchant_via_transaction(page, confirmed_server)
        page.goto(f"{confirmed_server}/s/ledger/merchants?edit=1")

    first_row = page.locator("table tbody tr:not(.sheet-grid-filler)").first
    _open_inline_editor(page, first_row.locator("td").nth(1))  # Category column

    edit_row = page.locator("table tbody tr:not(.sheet-grid-filler)").first
    assert edit_row.locator("select[name='value']").is_visible()


def test_merchants_category_inline_edit_saves(page, confirmed_server):
    """Selecting a new category in the inline editor persists it."""
    page.goto(f"{confirmed_server}/s/ledger/merchants?edit=1")
    if page.locator("table tbody tr:not(.sheet-grid-filler)").count() == 0:
        _seed_merchant_via_transaction(page, confirmed_server)
        page.goto(f"{confirmed_server}/s/ledger/merchants?edit=1")

    first_row = page.locator("table tbody tr:not(.sheet-grid-filler)").first
    _open_inline_editor(page, first_row.locator("td").nth(1))  # Category column

    edit_row = page.locator("table tbody tr:not(.sheet-grid-filler)").first
    with page.expect_response(lambda r: r.request.method == "POST"):
        edit_row.locator("select[name='value']").select_option("Dining")

    # Row reverts to display showing the new category.
    page.wait_for_selector(
        "table tbody tr:not(.sheet-grid-filler) td:has-text('Dining')"
    )
    assert (
        "Dining"
        in page.locator("table tbody tr:not(.sheet-grid-filler)").first.inner_text()
    )


def test_merchants_search_filters_results(page, confirmed_server):
    """Search parameter filters merchant rows by name."""
    page.goto(f"{confirmed_server}/s/ledger/merchants")
    if page.locator("table tbody tr:not(.sheet-grid-filler)").count() == 0:
        _seed_merchant_via_transaction(page, confirmed_server)
        page.goto(f"{confirmed_server}/s/ledger/merchants")

    total_before = page.locator("table tbody tr:not(.sheet-grid-filler)").count()

    page.goto(f"{confirmed_server}/s/ledger/merchants?search=XYZZY_NO_MATCH_9999")
    assert page.locator("table tbody tr:not(.sheet-grid-filler)").count() == 0

    page.goto(f"{confirmed_server}/s/ledger/merchants")
    assert (
        page.locator("table tbody tr:not(.sheet-grid-filler)").count() == total_before
    )


# ---------------------------------------------------------------------------
# Categories page — dedicated sheet (add / rename / blocked delete)
# ---------------------------------------------------------------------------
#
# Categories management moved off the Merchants page onto its own sidebar page
# using the shared sheet UI. These tests add categories with names unique to
# this file (TempCat, DupCat, RenameMeCat, ProtectedCat) rather than touching
# any seeded defaults (Groceries, Dining, ...) that earlier tests rely on,
# since confirmed_server is module-scoped and shared across the whole file.


def _add_category(page, base_url, name):
    """Add a category via the Categories page stub-then-edit pattern."""
    page.goto(f"{base_url}/s/ledger/categories?edit=1")
    with page.expect_response(
        lambda r: "/categories/add" in r.url and r.request.method == "POST"
    ):
        page.locator("button", has_text="+ Add category").click()
    page.wait_for_load_state("networkidle")
    # The stub is created as "New category" (or "New category N"); rename it.
    stub_cell = page.locator("#categories-table td", has_text="New category").first
    _open_inline_editor(page, stub_cell)
    rename_input = page.locator("#categories-table input[name='value']")
    rename_input.wait_for()
    rename_input.fill(name)
    with page.expect_response(
        lambda r: "/rename" in r.url and r.request.method == "POST"
    ):
        rename_input.press("Enter")
    page.wait_for_load_state("networkidle")


def test_categories_moved_off_merchants_page(page, confirmed_server):
    """The Merchants page no longer embeds category management, and the sidebar
    links to the dedicated Categories page instead."""
    page.goto(f"{confirmed_server}/s/ledger/merchants")
    assert page.get_by_role("button", name="Manage categories").count() == 0
    assert page.get_by_role("link", name="Categories").count() >= 1


def test_categories_page_renders_sheet(page, confirmed_server):
    """The Categories page renders the shared sheet: a Category column header
    and (in edit mode) an add button."""
    page.goto(f"{confirmed_server}/s/ledger/categories?edit=1")
    assert page.locator("#categories-table thead th", has_text="Category").is_visible()
    assert page.locator("button", has_text="+ Add category").is_visible()


def test_categories_add_new_category(page, confirmed_server):
    """Adding a category adds a row and makes it available in the Merchants
    Category filter (which reads live from the categories table)."""
    _add_category(page, confirmed_server, "TempCat")
    assert page.locator("#categories-table", has_text="TempCat").is_visible()

    page.goto(f"{confirmed_server}/s/ledger/merchants")
    labels = (
        page.locator("input[name='category']").locator("xpath=..").all_inner_texts()
    )
    assert any("TempCat" in t for t in labels)


def test_categories_rename_to_duplicate_shows_inline_error(page, confirmed_server):
    """Renaming a category to an existing name shows an inline error row."""
    _add_category(page, confirmed_server, "DupCat")
    _add_category(page, confirmed_server, "DupCat2")

    cell = page.locator("#categories-table td", has_text="DupCat2").first
    _open_inline_editor(page, cell)

    rename_input = page.locator("#categories-table input[name='value']")
    rename_input.wait_for()
    rename_input.fill("DupCat")
    with page.expect_response(
        lambda r: "/rename" in r.url and r.request.method == "POST"
    ) as resp_info:
        rename_input.press("Enter")
    assert resp_info.value.status == 422
    assert "already exists" in page.locator("#categories-table").inner_text()


def test_categories_rename(page, confirmed_server):
    """Clicking a category cell opens its inline editor; saving renames it."""
    _add_category(page, confirmed_server, "RenameMeCat")

    cell = page.locator("#categories-table td", has_text="RenameMeCat").first
    _open_inline_editor(page, cell)

    rename_input = page.locator("#categories-table input[name='value']")
    rename_input.wait_for()
    rename_input.fill("RenamedCat")
    with page.expect_response(
        lambda r: "/rename" in r.url and r.request.method == "POST"
    ):
        rename_input.press("Enter")
    page.wait_for_load_state("networkidle")

    assert page.locator("#categories-table", has_text="RenamedCat").is_visible()
    assert page.locator("#categories-table td", has_text="RenameMeCat").count() == 0


def test_categories_blocked_delete_shows_breakdown(page, confirmed_server):
    """Deleting a category still referenced by a merchant is blocked and shows
    the usage breakdown instead of silently failing or deleting it."""
    _add_category(page, confirmed_server, "ProtectedCat")
    _seed_merchant_via_transaction(page, confirmed_server, category="ProtectedCat")

    page.goto(f"{confirmed_server}/s/ledger/categories?edit=1")
    row = page.locator("#categories-table tr", has_text="ProtectedCat")
    with page.expect_response(lambda r: "/delete-confirm" in r.url):
        row.get_by_title("Delete").click()
    with page.expect_response(
        lambda r: r.url.endswith("/delete") and r.request.method == "POST"
    ) as resp_info:
        row.get_by_title("Confirm delete").click()
    assert resp_info.value.status == 422

    tbody_text = page.locator("#categories-table").inner_text()
    assert "In use by" in tbody_text
    assert "1 merchant" in tbody_text

    # Category must still exist (delete was blocked).
    assert page.locator("#categories-table", has_text="ProtectedCat").is_visible()
