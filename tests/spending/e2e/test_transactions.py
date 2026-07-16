"""E2E tests for the Transactions tab."""

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Filter controls and navigation (empty database)
# ---------------------------------------------------------------------------


def test_transactions_account_filter_present(page, flask_server):
    """Account dropdown is rendered in the filter bar."""
    page.goto(f"{flask_server}/transactions")
    assert page.locator("select[name='account_id']").is_visible()


def test_transactions_category_filter_present(page, flask_server):
    """Category dropdown is rendered in the filter bar."""
    page.goto(f"{flask_server}/transactions")
    assert page.locator("select[name='category']").is_visible()


def test_transactions_status_filter_present(page, flask_server):
    """Status dropdown is rendered with the expected options."""
    page.goto(f"{flask_server}/transactions")
    status_select = page.locator("select[name='status']")
    assert status_select.is_visible()
    options = status_select.locator("option").all_inner_texts()
    assert "Categorized" in options
    assert "Uncategorized" in options
    assert "Corrected" in options


def test_transactions_search_input_present(page, flask_server):
    """Text search input is rendered."""
    page.goto(f"{flask_server}/transactions")
    assert page.locator("input[name='search']").is_visible()


def test_transactions_month_label_displayed(page, flask_server):
    """Current month/year label is shown (MM/YYYY format)."""
    page.goto(f"{flask_server}/transactions?year=2026&month=4")
    assert page.locator("text=/\\d{2}\\/\\d{4}/").first.is_visible()


def test_transactions_prev_arrow_navigates(page, flask_server):
    """← arrow navigates to the previous month and updates the URL."""
    page.goto(f"{flask_server}/transactions?year=2026&month=4")
    page.click("a:has-text('←')")
    page.wait_for_url("**/transactions**month=3**")


def test_transactions_next_arrow_navigates(page, flask_server):
    """→ arrow navigates to the next month and updates the URL."""
    page.goto(f"{flask_server}/transactions?year=2026&month=4")
    page.click("a:has-text('→')")
    page.wait_for_url("**/transactions**month=5**")


def test_transactions_table_has_expected_columns(page, flask_server):
    """Table header renders the expected columns."""
    page.goto(f"{flask_server}/transactions")
    header = page.locator("table thead tr")
    for col in ("Date", "Merchant", "Description", "Category", "Amount"):
        assert header.locator("th", has_text=col).is_visible(), f"Missing column: {col}"


# ---------------------------------------------------------------------------
# Data display (confirmed_server has 4 transactions in 04/2026)
# ---------------------------------------------------------------------------


def test_transactions_rows_visible_with_data(page, confirmed_server):
    """Transaction rows appear after a confirmed import."""
    page.goto(f"{confirmed_server}/transactions?year=2026&month=4")
    rows = page.locator("table tbody tr")
    assert rows.count() == 4


def test_transactions_row_shows_date_and_amount(page, confirmed_server):
    """Each row contains a date (column 0) and a dollar amount (column 5)."""
    page.goto(f"{confirmed_server}/transactions?year=2026&month=4")
    first_row = page.locator("table tbody tr").first
    cells = first_row.locator("td").all_inner_texts()
    assert "2026" in cells[0]  # date column
    assert "$" in cells[5]  # amount column


def test_transactions_merchant_names_visible(page, confirmed_server):
    """Seeded merchant names appear in the transaction table."""
    page.goto(f"{confirmed_server}/transactions?year=2026&month=4")
    body_text = page.locator("table tbody").inner_text().upper()
    assert "WHOLE FOODS" in body_text or "CHIPOTLE" in body_text


def test_transactions_edit_button_per_row(page, confirmed_server):
    """Each transaction row has an edit icon button."""
    page.goto(f"{confirmed_server}/transactions?year=2026&month=4")
    buttons = page.locator("table tbody tr button[hx-get*='edit-category']")
    assert buttons.count() == 4


def test_transactions_click_edit_shows_inline_form(page, confirmed_server):
    """Clicking the edit icon inserts an inline edit form row via HTMX."""
    page.goto(f"{confirmed_server}/transactions?year=2026&month=4")
    edit_btn = page.locator("table tbody tr button[hx-get*='edit-category']").first
    with page.expect_response(lambda r: "edit-category" in r.url):
        edit_btn.click()
    page.wait_for_selector('tr[id^="edit-"]')


def test_transactions_edit_form_has_category_select(page, confirmed_server):
    """Inline edit form contains a category <select> with options."""
    page.goto(f"{confirmed_server}/transactions?year=2026&month=4")
    edit_btn = page.locator("table tbody tr button[hx-get*='edit-category']").first
    with page.expect_response(lambda r: "edit-category" in r.url):
        edit_btn.click()
    edit_row = page.locator('tr[id^="edit-"]').first
    cat_select = edit_row.locator("select[name='category']")
    assert cat_select.is_visible()
    assert cat_select.locator("option").count() > 0


def test_transactions_edit_form_has_apply_to_merchant_checkbox(page, confirmed_server):
    """Inline edit form has the 'Apply to all from this merchant' checkbox."""
    page.goto(f"{confirmed_server}/transactions?year=2026&month=4")
    edit_btn = page.locator("table tbody tr button[hx-get*='edit-category']").first
    with page.expect_response(lambda r: "edit-category" in r.url):
        edit_btn.click()
    edit_row = page.locator('tr[id^="edit-"]').first
    assert edit_row.locator("input[name='apply_to_merchant']").is_visible()


def test_transactions_edit_form_cancel_removes_row(page, confirmed_server):
    """Clicking Cancel in the inline edit form removes the edit row."""
    page.goto(f"{confirmed_server}/transactions?year=2026&month=4")
    edit_btn = page.locator("table tbody tr button[hx-get*='edit-category']").first
    with page.expect_response(lambda r: "edit-category" in r.url):
        edit_btn.click()
    page.wait_for_selector('tr[id^="edit-"]')
    page.locator('tr[id^="edit-"] button', has_text="Cancel").click()
    assert page.locator('tr[id^="edit-"]').count() == 0


def test_transactions_filter_by_status_uncategorized(page, confirmed_server):
    """Filtering by 'Uncategorized' shows all 4 uncategorized rows."""
    page.goto(f"{confirmed_server}/transactions?year=2026&month=4&status=uncategorized")
    rows = page.locator("table tbody tr")
    assert rows.count() == 4


def test_transactions_filter_by_status_categorized_empty(page, confirmed_server):
    """Filtering by 'Categorized' shows no rows when all are uncategorized."""
    page.goto(f"{confirmed_server}/transactions?year=2026&month=4&status=categorized")
    rows = page.locator("table tbody tr")
    assert rows.count() == 0


def test_transactions_search_filters_rows(page, confirmed_server):
    """Text search filters the transaction list."""
    page.goto(f"{confirmed_server}/transactions?year=2026&month=4&search=WHOLE+FOODS")
    rows = page.locator("table tbody tr")
    assert rows.count() == 1
    assert "WHOLE FOODS" in rows.first.inner_text().upper()
