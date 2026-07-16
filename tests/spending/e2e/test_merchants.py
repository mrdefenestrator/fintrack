"""E2E tests for the Merchants tab."""

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Filter controls and table structure (empty database)
# ---------------------------------------------------------------------------


def test_merchants_search_input_present(page, flask_server):
    """Merchant search input is rendered."""
    page.goto(f"{flask_server}/merchants")
    assert page.locator("input[name='search']").is_visible()


def test_merchants_category_filter_present(page, flask_server):
    """Category dropdown filter is rendered."""
    page.goto(f"{flask_server}/merchants")
    assert page.locator("select[name='category']").is_visible()


def test_merchants_source_filter_present(page, flask_server):
    """Source dropdown filter is rendered with Auto and Manual options."""
    page.goto(f"{flask_server}/merchants")
    source_select = page.locator("select[name='source']")
    assert source_select.is_visible()
    options = source_select.locator("option").all_inner_texts()
    assert "Auto" in options
    assert "Manual" in options


def test_merchants_table_has_expected_columns(page, flask_server):
    """Merchants table renders all expected header columns."""
    page.goto(f"{flask_server}/merchants")
    header = page.locator("table thead tr")
    for col in ("Merchant", "Category", "Source", "Transactions", "Last Seen"):
        assert header.locator("th", has_text=col).is_visible(), f"Missing column: {col}"


def test_merchants_empty_db_has_no_data_rows(page, flask_server):
    """With no merchant cache entries the table body is empty."""
    page.goto(f"{flask_server}/merchants")
    assert page.locator("table tbody tr").count() == 0


# ---------------------------------------------------------------------------
# Data display and inline editing (confirmed_server)
# ---------------------------------------------------------------------------


def test_merchants_empty_after_import_before_categorization(page, confirmed_server):
    """Merchants table is empty right after import since the API is disabled."""
    page.goto(f"{confirmed_server}/merchants")
    assert page.locator("table tbody tr").count() == 0


def test_merchants_appear_after_transaction_correction(page, confirmed_server):
    """Correcting a transaction with 'apply to merchant' populates the Merchants tab.

    Flow:
      1. Go to Transactions (April 2026).
      2. Click the edit icon on the first row.
      3. Select 'Groceries', check 'Apply to all from this merchant'.
      4. Save → server returns 204 with HX-Redirect back to /transactions.
      5. Navigate to /merchants → the merchant now appears.
    """
    # Step 1: open Transactions for April 2026
    page.goto(f"{confirmed_server}/transactions?year=2026&month=4")
    page.wait_for_selector("table tbody tr")

    # Step 2: click the edit icon on the first row
    edit_btn = page.locator("table tbody tr button[hx-get*='edit-category']").first
    with page.expect_response(lambda r: "edit-category" in r.url):
        edit_btn.click()
    page.wait_for_selector('tr[id^="edit-"]')

    # Step 3: select a category and check apply-to-merchant
    edit_row = page.locator('tr[id^="edit-"]').first
    edit_row.locator("select[name='category']").select_option("Groceries")
    edit_row.locator("input[name='apply_to_merchant']").check()

    # Step 4: save — server returns 204 with HX-Redirect
    with page.expect_response(lambda r: r.status == 204):
        edit_row.locator("button[type='submit']").click()
    page.wait_for_url("**/transactions**")
    page.wait_for_load_state("networkidle")

    # Step 5: merchants tab now has one entry
    page.goto(f"{confirmed_server}/merchants")
    rows = page.locator("table tbody tr")
    assert rows.count() >= 1
    row_text = rows.first.inner_text()
    assert "Groceries" in row_text
    assert "manual" in row_text.lower()


def test_merchants_edit_form_appears_on_edit_button_click(page, confirmed_server):
    """Clicking the edit icon on a merchant row opens an inline edit form."""
    page.goto(f"{confirmed_server}/merchants")

    if page.locator("table tbody tr").count() == 0:
        pytest.skip(
            "No merchants present — run after test_merchants_appear_after_transaction_correction"
        )

    edit_btn = page.locator("table tbody tr button[hx-get*='/edit']").first
    with page.expect_response(lambda r: "/merchants" in r.url and "/edit" in r.url):
        edit_btn.click()

    page.wait_for_selector("table tbody tr.bg-blue-50")
    edit_row = page.locator("table tbody tr.bg-blue-50")
    assert edit_row.locator("select[name='category']").is_visible()
    assert edit_row.locator("button", has_text="Save").is_visible()
    assert edit_row.locator("button", has_text="Cancel").is_visible()


def test_merchants_search_filters_results(page, confirmed_server):
    """Search parameter filters merchant rows by name."""
    page.goto(f"{confirmed_server}/merchants")
    if page.locator("table tbody tr").count() == 0:
        pytest.skip(
            "No merchants present — run after test_merchants_appear_after_transaction_correction"
        )

    total_before = page.locator("table tbody tr").count()

    # Navigate with a no-match search → table should be empty
    page.goto(f"{confirmed_server}/merchants?search=XYZZY_NO_MATCH_9999")
    assert page.locator("table tbody tr").count() == 0

    # Clear search → all rows return
    page.goto(f"{confirmed_server}/merchants")
    assert page.locator("table tbody tr").count() == total_before
