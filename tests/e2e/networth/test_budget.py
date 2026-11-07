"""Budget table e2e tests — CRUD, filters, period toggle."""

import pytest

from tests.e2e.networth.conftest import enable_edit_mode

pytestmark = pytest.mark.e2e


def test_budget_view_shows_data(page, flask_server):
    """Budget page shows the test fixture data rows."""
    page.goto(f"{flask_server}/s/test_finances/budget")
    tbody = page.locator("#budget-table")
    assert tbody.get_by_text("Salary", exact=True).first.is_visible()
    assert tbody.get_by_text("Rent", exact=True).first.is_visible()
    assert tbody.get_by_text("Groceries", exact=True).first.is_visible()


def test_budget_shows_total_row(page, flask_server):
    """Budget page shows a pinned Net total row."""
    page.goto(f"{flask_server}/s/test_finances/budget")
    total_cell = page.locator("#budget-table tr.total-row td:has-text('Net')")
    assert total_cell.count() == 1


def test_budget_grouped_by_category(page, flask_server):
    """Issue #51: one band per category, each with its own heading (carrying
    its subtotals) and its own column header row."""
    page.goto(f"{flask_server}/s/test_finances/budget")
    bands = page.locator("#budget-table tbody.sheet-group")
    labels = [
        t.strip().lower()
        for t in page.locator(".sheet-group-heading td:first-child").all_inner_texts()
    ]
    for category in ("income", "housing", "groceries", "insurance"):
        assert category in labels
    assert bands.count() == len(labels)
    assert page.locator(".sheet-group-header").count() == bands.count()
    # Rent lives in the Housing band.
    housing = page.locator("tbody.sheet-group[data-group='cat-housing']")
    assert housing.get_by_text("Rent", exact=True).is_visible()
    # Monthly subtotal sits in the band heading.
    assert (
        housing.locator(".sheet-group-heading")
        .get_by_text("($1,500.00)")
        .first.is_visible()
    )


def test_scroll_shadows_toggle_with_scroll_position(page, flask_server):
    """QA item 15: the sheet container ([data-sheet-scroll]) shows a shadow
    under the sticky header once scrolled down, and a shadow above the total
    row until scrolled to the bottom; both hide at their respective extremes.

    Shrink the viewport so the fixture's budget rows overflow the sheet and
    it actually becomes scrollable.
    """
    page.set_viewport_size({"width": 800, "height": 320})
    page.goto(f"{flask_server}/s/test_finances/budget")

    container = page.locator("[data-sheet-scroll]")
    top_shadow = page.locator(".sheet-scroll-shadow--top")
    bottom_shadow = page.locator(".sheet-scroll-shadow--bottom")

    # Sanity check the fixture actually overflows this viewport.
    scroll_height = container.evaluate("el => el.scrollHeight")
    client_height = container.evaluate("el => el.clientHeight")
    assert scroll_height > client_height, (
        "fixture/viewport no longer overflows — adjust viewport size"
    )

    # At the top: no top shadow; bottom shadow shown (more to scroll).
    assert "is-visible" not in (top_shadow.get_attribute("class") or "")
    assert "is-visible" in (bottom_shadow.get_attribute("class") or "")

    # Scroll to the bottom: top shadow shown, bottom shadow gone.
    container.evaluate("el => el.scrollTo(0, el.scrollHeight)")
    page.wait_for_timeout(100)
    assert "is-visible" in (top_shadow.get_attribute("class") or "")
    assert "is-visible" not in (bottom_shadow.get_attribute("class") or "")

    # Scroll back to the top: shadows revert.
    container.evaluate("el => el.scrollTo(0, 0)")
    page.wait_for_timeout(100)
    assert "is-visible" not in (top_shadow.get_attribute("class") or "")
    assert "is-visible" in (bottom_shadow.get_attribute("class") or "")


def test_budget_click_to_edit(page, flask_server):
    """Clicking a cell in edit mode opens an input."""
    page.goto(f"{flask_server}/s/test_finances/budget")
    enable_edit_mode(page)

    # Click on the Salary description cell
    salary_row = page.locator("#budget-table tr:has-text('Salary')").first
    # Description is the second cell (index 1)
    desc_cell = salary_row.locator("td").nth(1)
    desc_cell.click()

    # Should show an input
    input_el = page.locator("#budget-table input[name='value']")
    input_el.wait_for(state="visible")
    assert input_el.input_value() == "Salary"


def test_budget_update_cell(page, flask_server):
    """Editing a budget cell value saves it."""
    page.goto(f"{flask_server}/s/test_finances/budget")
    enable_edit_mode(page)

    salary_row = page.locator("#budget-table tr:has-text('Salary')").first
    desc_cell = salary_row.locator("td").nth(1)
    desc_cell.click()

    input_el = page.locator("#budget-table input[name='value']")
    input_el.wait_for(state="visible")

    # Explicitly click the input to register focus with Playwright's CDP layer.
    input_el.click()
    input_el.fill("Monthly Salary")

    # Click total row to blur; wait for the HTMX POST response before moving on
    with page.expect_response(
        lambda r: "/budget/update/" in r.url and r.request.method == "POST",
        timeout=5000,
    ):
        page.locator("#budget-table tr.total-row td").first.click()

    # Verify
    page.goto(f"{flask_server}/s/test_finances/budget")
    assert page.locator("#budget-table").get_by_text("Monthly Salary").is_visible()


def test_budget_add_expense(page, flask_server):
    """Adding a new expense entry via the stub-then-edit add button."""
    page.goto(f"{flask_server}/s/test_finances/budget")
    enable_edit_mode(page)

    with page.expect_response(
        lambda r: "/budget/add" in r.url and r.request.method == "POST"
    ):
        page.locator(
            "tbody[data-group='cat-housing'] button", has_text="+ Add expense"
        ).click()
    page.wait_for_load_state("networkidle")

    # Filed under the band it was added from.
    housing = page.locator("tbody[data-group='cat-housing']")
    assert housing.get_by_text("New entry").is_visible()


def test_budget_add_income(page, flask_server):
    """Adding a new income entry via the stub-then-edit add button."""
    page.goto(f"{flask_server}/s/test_finances/budget")
    enable_edit_mode(page)

    with page.expect_response(
        lambda r: "/budget/add" in r.url and r.request.method == "POST"
    ):
        page.locator(
            "tbody[data-group='cat-income'] button", has_text="+ Add income"
        ).click()
    page.wait_for_load_state("networkidle")

    income = page.locator("tbody[data-group='cat-income']")
    assert income.get_by_text("New entry").is_visible()


def test_budget_delete(page, flask_server):
    """Deleting a budget entry removes it."""
    page.goto(f"{flask_server}/s/test_finances/budget")
    enable_edit_mode(page)

    # Find the Salary row and click delete
    salary_row = page.locator("#budget-table tr:has-text('Salary')").first
    salary_row.locator("button[title='Delete']").click()

    # Confirm delete
    confirm_btn = salary_row.locator("button[title='Confirm delete']")
    confirm_btn.wait_for(state="visible")
    confirm_btn.click()
    page.wait_for_timeout(500)

    # Verify - reload page (exclude add row, whose type select contains "salary" as an option)
    page.goto(f"{flask_server}/s/test_finances/budget")
    assert (
        page.locator("#budget-table tr:not([data-add-row])")
        .filter(has_text="Salary")
        .count()
        == 0
    )


def test_budget_filter_by_kind(page, flask_server):
    """Filtering by kind shows only matching entries."""
    page.goto(f"{flask_server}/s/test_finances/budget")

    # Open Kind filter
    page.locator(".sm\\:flex button.filter-dropdown-trigger:has-text('Kind')").click()

    # Check income only
    page.locator(".sm\\:flex input[name='include_kind'][value='income']").click()
    page.wait_for_url("**/budget**include_kind**")

    tbody = page.locator("#budget-table")
    # Should show income items but not expenses
    assert tbody.get_by_text("Salary", exact=True).first.is_visible()
    # Rent is an expense - should not be shown in table rows
    assert tbody.locator("tr").filter(has_text="Rent").count() == 0


def test_budget_filter_by_kind_mobile(page, flask_server):
    """On a mobile viewport, the mobile filter panel toggle opens filters that work."""
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(f"{flask_server}/s/test_finances/budget")

    # Desktop bar is hidden; mobile trigger row is visible
    assert page.locator(".sm\\:hidden").first.is_visible()
    assert not page.locator(".sm\\:flex").first.is_visible()

    # Expand the mobile filter panel (shared bar: one control set, revealed
    # below sm when the Filters trigger is tapped)
    page.locator(".sm\\:hidden > button[type=button]").click()

    # The control set is now visible — open the Kind dropdown in it
    page.locator("button.filter-dropdown-trigger:has-text('Kind')").click()

    # Select income
    page.locator("input[name='include_kind'][value='income']").click()
    page.wait_for_url("**/budget**include_kind**")

    # Active count badge should appear in the mobile trigger row
    assert page.locator(".sm\\:hidden .tabular-nums").is_visible()

    # Correct rows shown
    tbody = page.locator("#budget-table")
    assert tbody.get_by_text("Salary", exact=True).first.is_visible()
    assert tbody.locator("tr").filter(has_text="Rent").count() == 0


def test_budget_sort(page, flask_server):
    """Clicking a band's column header sorts just that band (persisted in the
    URL per band)."""
    page.goto(f"{flask_server}/s/test_finances/budget")
    income = page.locator("tbody[data-group='cat-income']")

    def descriptions():
        return [
            t.strip()
            for t in income.locator(
                "tr[data-reorder-index] td:nth-child(2)"
            ).all_inner_texts()
        ]

    assert descriptions() == ["Salary", "Freelance", "Tax Refund"]
    income.locator(".sortable-th", has_text="Description").click()
    assert descriptions() == ["Freelance", "Salary", "Tax Refund"]
    assert income.locator(".sort-indicator").get_by_text("↑").count() == 1
    assert "sort_cat-income_col=c1" in page.url


def test_budget_monthly_and_annual_columns_visible(page, flask_server):
    """This month, Per month, and Annual columns are always visible."""
    page.goto(f"{flask_server}/s/test_finances/budget")

    headers = page.locator(".sheet-group-header").first.locator("th")
    header_texts = headers.all_inner_texts()
    assert "This month" in header_texts
    assert "Per month" in header_texts
    assert "Annual" in header_texts


def test_budget_when_one_time_date_picker(page, flask_server):
    """Clicking 'When' on a one_time row shows a date input."""
    page.goto(f"{flask_server}/s/test_finances/budget")
    enable_edit_mode(page)

    # Find the Tax Refund row (one_time) and click its When cell (col index 4)
    refund_row = page.locator("#budget-table tr:has-text('Tax Refund')").first
    when_cell = refund_row.locator("td").nth(4)
    when_cell.click()

    # Should show a text input for the date value
    date_input = page.locator("#budget-table input[name='value']")
    date_input.wait_for(state="visible")
    assert date_input.is_visible()


def test_budget_when_one_time_sets_date(page, flask_server):
    """Setting a date on a one_time entry via the date picker saves it."""
    page.goto(f"{flask_server}/s/test_finances/budget")
    enable_edit_mode(page)

    refund_row = page.locator("#budget-table tr:has-text('Tax Refund')").first
    when_cell = refund_row.locator("td").nth(4)
    when_cell.click()

    date_input = page.locator("#budget-table input[name='value']")
    date_input.wait_for(state="visible")

    # Explicitly click the input to register focus with Playwright's CDP layer.
    date_input.click()
    date_input.fill("2026-02-15")

    # Click total row to blur; wait for the HTMX POST response before moving on
    with page.expect_response(
        lambda r: "/budget/update/" in r.url and r.request.method == "POST",
        timeout=5000,
    ):
        page.locator("#budget-table tr.total-row td").first.click()

    # Reload and verify the date is displayed
    page.goto(f"{flask_server}/s/test_finances/budget")
    refund_row = page.locator("#budget-table tr:has-text('Tax Refund')").first
    assert refund_row.get_by_text("2026-02-15").is_visible()


def test_budget_when_monthly_day_edit(page, flask_server):
    """Clicking 'When' on a non-continuous monthly row shows compound editor."""
    page.goto(f"{flask_server}/s/test_finances/budget")
    enable_edit_mode(page)

    # Find the Salary row (monthly, dayOfMonth=1) and click its When cell
    salary_row = page.locator("#budget-table tr:has-text('Salary')").first
    when_cell = salary_row.locator("td").nth(4)
    when_cell.click()

    # Should show compound editor with dayOfMonth input (no continuous for income)
    editing_cell = salary_row.locator("td.cell-editing")
    day_input = editing_cell.locator("input[name='dayOfMonth']")
    day_input.wait_for(state="visible")
    assert day_input.input_value() == "1"
    assert not editing_cell.locator("input[name='continuous_cb']").is_visible()

    # Change to day 5
    day_input.fill("5")
    # Wait for the HTMX POST to land before re-checking (a fixed timeout
    # races the save under full-suite load)
    with page.expect_response(
        lambda r: "/budget/" in r.url and r.request.method == "POST",
        timeout=5000,
    ):
        page.keyboard.press("Enter")

    # Verify display shows 5th
    page.goto(f"{flask_server}/s/test_finances/budget")
    salary_row = page.locator("#budget-table tr:has-text('Salary')").first
    assert salary_row.get_by_text("5th").is_visible()


def test_budget_when_continuous_toggle_off(page, flask_server):
    """Unchecking continuous on a monthly entry and setting a day saves correctly."""
    page.goto(f"{flask_server}/s/test_finances/budget")
    enable_edit_mode(page)

    # Find the Groceries row (monthly, continuous=true) and click its When cell
    groceries_row = page.locator(
        "#budget-table tr[id^='budget-row-']:has-text('Groceries')"
    ).first
    when_cell = groceries_row.locator("td").nth(4)
    when_cell.click()

    # Should show compound editor with checkbox checked
    editing_cell = groceries_row.locator("td.cell-editing")
    cont_cb = editing_cell.locator("input[name='continuous_cb']")
    cont_cb.wait_for(state="visible")
    assert cont_cb.is_checked()

    # Day input should be readonly when continuous
    day_input = editing_cell.locator("input[name='dayOfMonth']")
    assert day_input.get_attribute("readonly") is not None

    # Uncheck continuous
    cont_cb.click()
    page.wait_for_timeout(200)

    # Day input should now be editable
    assert day_input.get_attribute("readonly") is None

    # Enter day 5
    day_input.fill("5")

    # Blur to save (focusout triggers save)
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)

    # Reload and verify display changed from "continuous" to "5th"
    page.goto(f"{flask_server}/s/test_finances/budget")
    groceries_row = page.locator(
        "#budget-table tr[id^='budget-row-']:has-text('Groceries')"
    ).first
    assert groceries_row.get_by_text("5th").is_visible()


def test_budget_when_continuous_toggle_on(page, flask_server):
    """Checking continuous on a monthly expense entry saves it correctly."""
    page.goto(f"{flask_server}/s/test_finances/budget")
    enable_edit_mode(page)

    # Find the Rent row (monthly expense, dayOfMonth=1, no continuous) and click its When cell
    rent_row = page.locator("#budget-row-3")
    when_cell = rent_row.locator("td").nth(4)
    when_cell.click()

    # Check continuous
    editing_cell = rent_row.locator("td.cell-editing")
    cont_cb = editing_cell.locator("input[name='continuous_cb']")
    cont_cb.wait_for(state="visible")
    assert not cont_cb.is_checked()
    cont_cb.click()
    # Checkbox @change triggers save via $nextTick + htmx.trigger
    page.wait_for_timeout(500)

    # Reload and verify display shows "continuous"
    page.goto(f"{flask_server}/s/test_finances/budget")
    rent_row = page.locator("#budget-row-3")
    assert rent_row.get_by_text("continuous").is_visible()


def test_budget_when_annual_compound_edit(page, flask_server):
    """Editing the 'When' for an annual entry via compound editor saves month+day."""
    page.goto(f"{flask_server}/s/test_finances/budget")
    enable_edit_mode(page)

    # Find the Car Insurance row (annual, month=3, dayOfMonth=15) and click its When cell
    row = page.locator("#budget-table tr:has-text('Car Insurance')").first
    when_cell = row.locator("td").nth(4)
    when_cell.click()

    # Should show compound editor with month select and day input
    editing_cell = row.locator("td.cell-editing")
    month_select = editing_cell.locator("select[name='month']")
    month_select.wait_for(state="visible")
    day_input = editing_cell.locator("input[name='dayOfMonth']")
    assert day_input.is_visible()

    # Change month to April (4) and day to 10
    month_select.select_option("4")
    # Explicitly click day_input to register CDP focus, then fill
    day_input.click()
    day_input.fill("10")

    # Press Enter to trigger keydown[keyCode==13] → HTMX POST; wait for response
    with page.expect_response(
        lambda r: "/budget/when/" in r.url and r.request.method == "POST",
        timeout=5000,
    ):
        page.keyboard.press("Enter")

    # Reload and verify display shows "Apr 10th"
    page.goto(f"{flask_server}/s/test_finances/budget")
    row = page.locator("#budget-table tr:has-text('Car Insurance')").first
    assert row.get_by_text("Apr 10th").is_visible()
