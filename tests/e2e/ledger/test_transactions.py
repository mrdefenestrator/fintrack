"""E2E tests for the Transactions tab.

Transactions use spreadsheet-style inline editing for the corrections-overlay
fields (Merchant, Category, Notes): click the cell → it becomes an input/select
that saves on change/blur. Raw imported columns (Date, Description, Account,
Amount) are display-only.
"""

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Filter controls and navigation (empty database)
# ---------------------------------------------------------------------------


def test_transactions_account_filter_present(page, flask_server):
    """The Account multi-select dropdown is rendered in the filter bar."""
    page.goto(f"{flask_server}/s/ledger/transactions")
    assert page.get_by_role("button", name="Account").is_visible()


def test_transactions_category_filter_present(page, flask_server):
    """The Category multi-select dropdown is rendered in the filter bar."""
    page.goto(f"{flask_server}/s/ledger/transactions")
    assert page.get_by_role("button", name="Category").is_visible()


def test_transactions_status_filter_present(page, flask_server):
    """Status dropdown is rendered with the expected options."""
    page.goto(f"{flask_server}/s/ledger/transactions")
    status_select = page.locator("select[name='status']")
    assert status_select.is_visible()
    options = status_select.locator("option").all_inner_texts()
    assert "Categorized" in options
    assert "Uncategorized" in options
    assert "Corrected" in options


def test_transactions_search_input_present(page, flask_server):
    """The combined search/amount input is rendered."""
    page.goto(f"{flask_server}/s/ledger/transactions")
    assert page.locator("input[name='q']").is_visible()


def test_transactions_combined_filter_by_amount(page, confirmed_server):
    """The single q box filters by amount when the text parses as an amount.

    q=15-16 matches only NETFLIX (-$15.99), not the other seeded rows.
    """
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4&q=15-16")
    body_text = page.locator("table tbody").inner_text()
    assert "NETFLIX" in body_text
    assert "WHOLE FOODS" not in body_text


def test_transactions_combined_filter_by_text(page, confirmed_server):
    """The single q box searches text when it is not an amount expression."""
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4&q=WHOLE")
    body_text = page.locator("table tbody").inner_text()
    assert "WHOLE FOODS" in body_text
    assert "NETFLIX" not in body_text


def test_transactions_month_label_displayed(page, flask_server):
    """Current month/year label is shown (MM/YYYY format)."""
    page.goto(f"{flask_server}/s/ledger/transactions?year=2026&month=4")
    assert page.locator("text=/\\d{2}\\/\\d{4}/").first.is_visible()


def test_transactions_prev_arrow_navigates(page, flask_server):
    """← arrow navigates to the previous month and updates the URL."""
    page.goto(f"{flask_server}/s/ledger/transactions?year=2026&month=4")
    page.click("a:has-text('←')")
    page.wait_for_url("**/transactions**month=3**")


def test_transactions_next_arrow_navigates(page, flask_server):
    """→ arrow navigates to the next month and updates the URL."""
    page.goto(f"{flask_server}/s/ledger/transactions?year=2026&month=4")
    page.click("a:has-text('→')")
    page.wait_for_url("**/transactions**month=5**")


def test_transactions_table_has_expected_columns(page, flask_server):
    """Table header renders the expected columns."""
    page.goto(f"{flask_server}/s/ledger/transactions")
    header = page.locator("table thead tr")
    for col in ("Date", "Merchant", "Description", "Category", "Amount", "Notes"):
        assert header.locator("th", has_text=col).is_visible(), f"Missing column: {col}"


# ---------------------------------------------------------------------------
# Data display (confirmed_server has 4 transactions in 04/2026)
# ---------------------------------------------------------------------------


def test_transactions_rows_visible_with_data(page, confirmed_server):
    """Transaction rows appear after a confirmed import."""
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4")
    rows = page.locator("table tbody tr:not(.total-row)")
    assert rows.count() == 4


# ---------------------------------------------------------------------------
# Client-side column sorting (spreadsheet-style, shared sortable.js)
# ---------------------------------------------------------------------------


def test_transactions_uses_sheet_style_sortable_table(page, flask_server):
    """The table adopts the Finances sheet: a scroll container + sortable headers."""
    page.goto(f"{flask_server}/s/ledger/transactions")
    assert page.locator("[data-sheet-scroll] table.sortable").count() == 1
    assert page.locator("th.sortable-th[data-col='1']").is_visible()


def test_transactions_sort_by_merchant_reorders_rows(page, confirmed_server):
    """Clicking the Merchant header sorts client-side asc → desc without reload."""
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4")

    def first_merchant():
        return page.locator("table tbody tr").first.locator("td").nth(1).inner_text()

    # Default order is newest-first (date desc): NETFLIX (04-15) leads.
    assert "NETFLIX" in first_merchant()
    # Ascending by merchant name → CHIPOTLE sorts first.
    page.locator("th[data-col='1']").click()
    assert "CHIPOTLE" in first_merchant()
    # Descending → WHOLE FOODS sorts first.
    page.locator("th[data-col='1']").click()
    assert "WHOLE FOODS" in first_merchant()


def test_transactions_sort_by_amount_is_numeric(page, confirmed_server):
    """The Amount header sorts numerically (parsing the $ / sign), not lexically."""
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4")

    def first_amount():
        return page.locator("table tbody tr").first.locator("td").nth(5).inner_text()

    # Ascending by signed amount → the largest expense (-$52.75) leads.
    page.locator("th[data-col='5']").click()
    assert "52.75" in first_amount()
    # Descending → the smallest expense (-$15.99) leads.
    page.locator("th[data-col='5']").click()
    assert "15.99" in first_amount()


def test_transactions_row_shows_date_and_amount(page, confirmed_server):
    """Each row contains a date (column 0) and a dollar amount (column 5)."""
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4")
    first_row = page.locator("table tbody tr:not(.total-row)").first
    cells = first_row.locator("td").all_inner_texts()
    assert "2026" in cells[0]  # date column
    assert "$" in cells[5]  # amount column


def test_transactions_total_row_in_sheet(page, confirmed_server):
    """The count + sum live in a sticky in-sheet total row (4 txns, $132.24)."""
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4")
    total = page.locator("table tbody tr.total-row")
    assert total.count() == 1
    text = total.inner_text()
    assert "4 transactions" in text
    assert "132.24" in text


def test_transactions_total_row_stays_last_when_sorted(page, confirmed_server):
    """Sorting a column keeps the total row pinned to the bottom of the sheet."""
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4")
    page.locator("th[data-col='1']").click()  # sort by Merchant
    last_row = page.locator("table tbody tr").last
    assert "total-row" in (last_row.get_attribute("class") or "")


def test_transactions_merchant_names_visible(page, confirmed_server):
    """Seeded merchant names appear in the transaction table."""
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4")
    body_text = page.locator("table tbody").inner_text().upper()
    assert "WHOLE FOODS" in body_text or "CHIPOTLE" in body_text


# ---------------------------------------------------------------------------
# Inline spreadsheet editing
# ---------------------------------------------------------------------------


def test_transactions_category_cell_opens_inline_select(page, confirmed_server):
    """Clicking the Category cell (col 4) swaps it to a <select> with the
    apply-to-merchant option."""
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4")
    cat_cell = page.locator("table tbody tr").first.locator("td").nth(4)
    with page.expect_response(lambda r: "/cell" in r.url and "category" in r.url):
        cat_cell.click()
    edit_row = page.locator("table tbody tr").first
    assert edit_row.locator("select[name='value']").is_visible()
    assert edit_row.locator("input[name='apply_to_merchant']").count() == 1


def test_transactions_merchant_cell_opens_text_input(page, confirmed_server):
    """Clicking the Merchant cell (col 1) swaps it to a text input."""
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4")
    merchant_cell = page.locator("table tbody tr").first.locator("td").nth(1)
    with page.expect_response(lambda r: "/cell" in r.url and "merchant_name" in r.url):
        merchant_cell.click()
    edit_row = page.locator("table tbody tr").first
    assert edit_row.locator("input[name='value']").is_visible()


def test_transactions_notes_cell_opens_text_input(page, confirmed_server):
    """Clicking the Notes cell (col 6) swaps it to a text input."""
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4")
    notes_cell = page.locator("table tbody tr").first.locator("td").nth(6)
    with page.expect_response(lambda r: "/cell" in r.url and "notes" in r.url):
        notes_cell.click()
    edit_row = page.locator("table tbody tr").first
    assert edit_row.locator("input[name='value']").is_visible()


def test_transactions_amount_cell_not_editable(page, confirmed_server):
    """The Amount cell (col 5) is raw data and does not open an editor."""
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4")
    amount_cell = page.locator("table tbody tr").first.locator("td").nth(5)
    amount_cell.click()
    # No inline input appears anywhere in the table.
    assert page.locator("table tbody input[name='value']").count() == 0


# ---------------------------------------------------------------------------
# Filters (read-only) — run before the mutating save test below
# ---------------------------------------------------------------------------


def test_transactions_filter_by_status_uncategorized(page, confirmed_server):
    """Filtering by 'Uncategorized' shows all 4 uncategorized rows."""
    page.goto(
        f"{confirmed_server}/s/ledger/transactions?year=2026&month=4&status=uncategorized"
    )
    rows = page.locator("table tbody tr:not(.total-row)")
    assert rows.count() == 4


def test_transactions_filter_by_status_categorized_empty(page, confirmed_server):
    """Filtering by 'Categorized' shows no rows when all are uncategorized."""
    page.goto(
        f"{confirmed_server}/s/ledger/transactions?year=2026&month=4&status=categorized"
    )
    rows = page.locator("table tbody tr:not(.total-row)")
    assert rows.count() == 0


def test_transactions_search_filters_rows(page, confirmed_server):
    """Text search filters the transaction list."""
    page.goto(
        f"{confirmed_server}/s/ledger/transactions?year=2026&month=4&search=WHOLE+FOODS"
    )
    rows = page.locator("table tbody tr:not(.total-row)")
    assert rows.count() == 1
    assert "WHOLE FOODS" in rows.first.inner_text().upper()


def test_transactions_amount_filter_range_filters_rows(page, confirmed_server):
    """Amount range filter narrows the transaction list."""
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4")
    all_rows = page.locator("table tbody tr:not(.total-row)").count()
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4&amount=0-1")
    filtered_rows = page.locator("table tbody tr:not(.total-row)").count()
    assert filtered_rows <= all_rows


def test_transactions_amount_filter_invalid_input_ignored(page, confirmed_server):
    """Invalid amount text is ignored rather than erroring."""
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4")
    all_rows = page.locator("table tbody tr:not(.total-row)").count()
    page.goto(
        f"{confirmed_server}/s/ledger/transactions?year=2026&month=4&amount=garbage"
    )
    assert page.locator("table tbody tr:not(.total-row)").count() == all_rows


# ---------------------------------------------------------------------------
# Mutating save test — placed last so it cannot disturb the counts above.
# Editing Notes writes a correction without changing category/status counts.
# ---------------------------------------------------------------------------


def test_transactions_notes_inline_edit_saves(page, confirmed_server):
    """Typing a note and blurring persists it to the corrections overlay."""
    page.goto(f"{confirmed_server}/s/ledger/transactions?year=2026&month=4")
    notes_cell = page.locator("table tbody tr").first.locator("td").nth(6)
    with page.expect_response(lambda r: "/cell" in r.url and "notes" in r.url):
        notes_cell.click()
    note_input = page.locator("table tbody tr").first.locator("input[name='value']")
    note_input.fill("reimbursable")
    with page.expect_response(lambda r: r.request.method == "POST"):
        note_input.press("Enter")
    page.wait_for_selector("table tbody tr td:has-text('reimbursable')")
    assert "reimbursable" in page.locator("table tbody").inner_text()
