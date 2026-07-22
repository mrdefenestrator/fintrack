"""E2E tests for the Merchants tab.

The Merchants list uses the same spreadsheet-style inline editing as the
accounts/budget/assets sheets: click the Category cell → it becomes a <select>
that auto-saves on change (no far-right edit button, no far-left Save button).
"""

import pytest

pytestmark = pytest.mark.e2e


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
    assert page.locator("table tbody tr").count() == 0


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
    assert page.locator("table tbody tr").count() == 0


def _seed_merchant_via_transaction(page, base_url, category="Groceries"):
    """Populate the merchant cache by categorizing a transaction with the
    inline Category editor's apply-to-merchant option checked."""
    page.goto(f"{base_url}/s/ledger/transactions?year=2026&month=4")
    page.wait_for_selector("table tbody tr")

    # Open the Category cell (5th column) on the first row.
    first_row = page.locator("table tbody tr").first
    cat_cell = first_row.locator("td").nth(4)
    with page.expect_response(lambda r: "/cell" in r.url and "category" in r.url):
        cat_cell.click()
    # Let the swap fully settle before interacting further: under heavier
    # load (e.g. after driving the categories panel elsewhere on the page)
    # the response event can fire slightly before htmx finishes processing
    # the newly-swapped row, so an immediate select_option's change event
    # would be missed.
    page.wait_for_load_state("networkidle")

    edit_row = page.locator("table tbody tr").first
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
    rows = page.locator("table tbody tr")
    assert rows.count() >= 1
    row_text = rows.first.inner_text()
    assert "Groceries" in row_text
    assert "manual" in row_text.lower()


def test_merchants_category_cell_opens_inline_select(page, confirmed_server):
    """Clicking a merchant's Category cell swaps it to an inline <select>."""
    page.goto(f"{confirmed_server}/s/ledger/merchants")
    if page.locator("table tbody tr").count() == 0:
        _seed_merchant_via_transaction(page, confirmed_server)
        page.goto(f"{confirmed_server}/s/ledger/merchants")

    first_row = page.locator("table tbody tr").first
    cat_cell = first_row.locator("td").nth(1)  # Category is the 2nd column
    with page.expect_response(lambda r: "/cell" in r.url):
        cat_cell.click()

    edit_row = page.locator("table tbody tr").first
    assert edit_row.locator("select[name='value']").is_visible()


def test_merchants_category_inline_edit_saves(page, confirmed_server):
    """Selecting a new category in the inline editor persists it."""
    page.goto(f"{confirmed_server}/s/ledger/merchants")
    if page.locator("table tbody tr").count() == 0:
        _seed_merchant_via_transaction(page, confirmed_server)
        page.goto(f"{confirmed_server}/s/ledger/merchants")

    first_row = page.locator("table tbody tr").first
    cat_cell = first_row.locator("td").nth(1)
    with page.expect_response(lambda r: "/cell" in r.url):
        cat_cell.click()
    # Let the swap fully settle before selecting: the /cell response can fire
    # slightly before htmx finishes wiring the newly-swapped <select>'s hx-post,
    # so an immediate select_option's change event would be missed and no POST
    # sent (same race the seed helper guards against above).
    page.wait_for_load_state("networkidle")

    edit_row = page.locator("table tbody tr").first
    with page.expect_response(lambda r: r.request.method == "POST"):
        edit_row.locator("select[name='value']").select_option("Dining")

    # Row reverts to display showing the new category.
    page.wait_for_selector("table tbody tr td:has-text('Dining')")
    assert "Dining" in page.locator("table tbody tr").first.inner_text()


def test_merchants_search_filters_results(page, confirmed_server):
    """Search parameter filters merchant rows by name."""
    page.goto(f"{confirmed_server}/s/ledger/merchants")
    if page.locator("table tbody tr").count() == 0:
        _seed_merchant_via_transaction(page, confirmed_server)
        page.goto(f"{confirmed_server}/s/ledger/merchants")

    total_before = page.locator("table tbody tr").count()

    page.goto(f"{confirmed_server}/s/ledger/merchants?search=XYZZY_NO_MATCH_9999")
    assert page.locator("table tbody tr").count() == 0

    page.goto(f"{confirmed_server}/s/ledger/merchants")
    assert page.locator("table tbody tr").count() == total_before


# ---------------------------------------------------------------------------
# Manage categories panel (add / rename / blocked delete)
# ---------------------------------------------------------------------------
#
# These tests add categories with names unique to this file (TempCat,
# RenamedCat, ProtectedCat) rather than touching any of the seeded defaults
# (Groceries, Dining, ...) that earlier tests in this module rely on, since
# confirmed_server is module-scoped and shared across the whole file.


def _open_categories_panel(page, base_url):
    page.goto(f"{base_url}/s/ledger/merchants")
    page.get_by_role("button", name="Manage categories").click()
    page.wait_for_selector("#categories-panel-body input[name='name']")


def test_categories_panel_collapsed_by_default(page, confirmed_server):
    """The Manage-categories panel starts collapsed; its add form is hidden
    until the toggle is clicked."""
    page.goto(f"{confirmed_server}/s/ledger/merchants")
    assert page.get_by_role("button", name="Manage categories").is_visible()
    assert not page.locator("#categories-panel-body input[name='name']").is_visible()


def test_categories_panel_add_new_category(page, confirmed_server):
    """Adding a category via the panel makes it available in the Category
    filter dropdown (which reads live from the categories table)."""
    _open_categories_panel(page, confirmed_server)

    name_input = page.locator("#categories-panel-body input[name='name']")
    name_input.fill("TempCat")
    with page.expect_response(
        lambda r: "/categories/add" in r.url and r.request.method == "POST"
    ):
        name_input.press("Enter")
    page.wait_for_load_state("networkidle")

    # The Category filter is now a custom radio dropdown; its option labels
    # read live from the categories table.
    labels = (
        page.locator("input[name='category']").locator("xpath=..").all_inner_texts()
    )
    assert any("TempCat" in t for t in labels)


def test_categories_panel_add_duplicate_shows_inline_error(page, confirmed_server):
    """Adding a category name that already exists shows an inline error
    instead of navigating away, and does not create a duplicate."""
    _open_categories_panel(page, confirmed_server)
    name_input = page.locator("#categories-panel-body input[name='name']")
    name_input.fill("DupCat")
    with page.expect_response(
        lambda r: "/categories/add" in r.url and r.request.method == "POST"
    ):
        name_input.press("Enter")
    page.wait_for_load_state("networkidle")

    _open_categories_panel(page, confirmed_server)
    name_input = page.locator("#categories-panel-body input[name='name']")
    name_input.fill("DupCat")
    with page.expect_response(
        lambda r: "/categories/add" in r.url and r.request.method == "POST"
    ) as resp_info:
        name_input.press("Enter")
    assert resp_info.value.status == 422
    assert "already exists" in page.locator("#categories-panel-body").inner_text()
    labels = [
        t.strip()
        for t in page.locator("input[name='category']")
        .locator("xpath=..")
        .all_inner_texts()
    ]
    assert labels.count("DupCat") == 1


def test_categories_panel_rename_cascades_to_filter(page, confirmed_server):
    """Renaming a category updates the Category filter options (cascade
    through the categories table the filter reads from)."""
    _open_categories_panel(page, confirmed_server)
    name_input = page.locator("#categories-panel-body input[name='name']")
    name_input.fill("RenameMeCat")
    with page.expect_response(
        lambda r: "/categories/add" in r.url and r.request.method == "POST"
    ):
        name_input.press("Enter")
    page.wait_for_load_state("networkidle")

    _open_categories_panel(page, confirmed_server)
    row = page.locator("#categories-panel-body li", has_text="RenameMeCat")
    with page.expect_response(lambda r: "/edit" in r.url):
        row.get_by_text("RenameMeCat", exact=True).click()
    page.wait_for_load_state("networkidle")

    rename_input = page.locator("#categories-panel-body input[name='value']")
    rename_input.wait_for()
    rename_input.fill("RenamedCat")
    with page.expect_response(
        lambda r: "/rename" in r.url and r.request.method == "POST"
    ):
        rename_input.press("Enter")
    # A successful rename carries HX-Refresh, which triggers a full page
    # reload; wait for that navigation to complete (networkidle can resolve
    # mid-navigation and race the reload) before reading the refreshed page.
    page.wait_for_load_state("load")
    # The Category radios live inside a collapsed (display:none) dropdown, so
    # wait for them to be attached rather than visible.
    page.wait_for_selector("input[name='category']", state="attached")

    labels = [
        t.strip()
        for t in page.locator("input[name='category']")
        .locator("xpath=..")
        .all_inner_texts()
    ]
    assert "RenamedCat" in labels
    assert "RenameMeCat" not in labels


def test_categories_panel_blocked_delete_shows_breakdown(page, confirmed_server):
    """Deleting a category still referenced by a merchant is blocked and
    shows the usage breakdown instead of silently failing or deleting it."""
    _open_categories_panel(page, confirmed_server)
    name_input = page.locator("#categories-panel-body input[name='name']")
    name_input.fill("ProtectedCat")
    with page.expect_response(
        lambda r: "/categories/add" in r.url and r.request.method == "POST"
    ):
        name_input.press("Enter")
    page.wait_for_load_state("networkidle")

    _seed_merchant_via_transaction(page, confirmed_server, category="ProtectedCat")

    _open_categories_panel(page, confirmed_server)
    row = page.locator("#categories-panel-body li", has_text="ProtectedCat")
    row.get_by_title("Delete category").click()
    with page.expect_response(
        lambda r: "/delete" in r.url and r.request.method == "POST"
    ) as resp_info:
        row.get_by_title("Confirm delete").click()
    assert resp_info.value.status == 422

    panel_text = page.locator("#categories-panel-body").inner_text()
    assert "In use by" in panel_text
    assert "1 merchant" in panel_text

    # Category must still exist (delete was blocked).
    labels = [
        t.strip()
        for t in page.locator("input[name='category']")
        .locator("xpath=..")
        .all_inner_texts()
    ]
    assert "ProtectedCat" in labels
