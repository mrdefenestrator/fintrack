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
    assert page.locator("select[name='category']").is_visible()


def test_merchants_source_filter_present(page, flask_server):
    """Source dropdown filter is rendered with Auto and Manual options."""
    page.goto(f"{flask_server}/s/ledger/merchants")
    source_select = page.locator("select[name='source']")
    assert source_select.is_visible()
    options = source_select.locator("option").all_inner_texts()
    assert "Auto" in options
    assert "Manual" in options


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


# ---------------------------------------------------------------------------
# Data display and inline editing (confirmed_server)
# ---------------------------------------------------------------------------


def test_merchants_empty_after_import_before_categorization(page, confirmed_server):
    """Merchants table is empty right after import since the API is disabled."""
    page.goto(f"{confirmed_server}/s/ledger/merchants")
    assert page.locator("table tbody tr").count() == 0


def _seed_merchant_via_transaction(page, base_url):
    """Populate the merchant cache by categorizing a transaction with the
    inline Category editor's apply-to-merchant option checked."""
    page.goto(f"{base_url}/s/ledger/transactions?year=2026&month=4")
    page.wait_for_selector("table tbody tr")

    # Open the Category cell (5th column) on the first row.
    first_row = page.locator("table tbody tr").first
    cat_cell = first_row.locator("td").nth(4)
    with page.expect_response(lambda r: "/cell" in r.url and "category" in r.url):
        cat_cell.click()

    edit_row = page.locator("table tbody tr").first
    # apply-to-merchant is checked by default; selecting a category saves and
    # (because it's merchant-wide) redirects/reloads the list.
    edit_row.locator("input[name='apply_to_merchant']").check()
    with page.expect_response(lambda r: r.request.method == "POST"):
        edit_row.locator("select[name='value']").select_option("Groceries")
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
