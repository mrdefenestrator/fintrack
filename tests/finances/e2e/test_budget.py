"""Budget table e2e tests — CRUD, filters, period toggle."""

import pytest

from tests.e2e.conftest import enable_edit_mode

pytestmark = pytest.mark.e2e


def test_budget_view_shows_data(page, flask_server):
    """Budget page shows the test fixture data rows."""
    page.goto(f"{flask_server}/f/test_finances/budget")
    tbody = page.locator("#budget-tbody")
    assert tbody.get_by_text("Salary", exact=True).first.is_visible()
    assert tbody.get_by_text("Rent", exact=True).first.is_visible()
    assert tbody.get_by_text("Groceries", exact=True).first.is_visible()


def test_budget_shows_total_row(page, flask_server):
    """Budget page shows a total row."""
    page.goto(f"{flask_server}/f/test_finances/budget")
    total_cell = page.locator("#budget-tbody td:has-text('Total')")
    assert total_cell.count() >= 1


def test_budget_click_to_edit(page, flask_server):
    """Clicking a cell in edit mode opens an input."""
    page.goto(f"{flask_server}/f/test_finances/budget")
    enable_edit_mode(page)

    # Click on the Salary description cell
    salary_row = page.locator("#budget-tbody tr:has-text('Salary')").first
    # Description is the third cell (index 2)
    desc_cell = salary_row.locator("td").nth(2)
    desc_cell.click()

    # Should show an input
    input_el = page.locator("#budget-tbody input[name='value']")
    input_el.wait_for(state="visible")
    assert input_el.input_value() == "Salary"


def test_budget_update_cell(page, flask_server):
    """Editing a budget cell value saves it."""
    page.goto(f"{flask_server}/f/test_finances/budget")
    enable_edit_mode(page)

    salary_row = page.locator("#budget-tbody tr:has-text('Salary')").first
    desc_cell = salary_row.locator("td").nth(2)
    desc_cell.click()

    input_el = page.locator("#budget-tbody input[name='value']")
    input_el.wait_for(state="visible")

    # Explicitly click the input to register focus with Playwright's CDP layer.
    input_el.click()
    input_el.fill("Monthly Salary")

    # Click total row to blur; wait for the HTMX POST response before moving on
    with page.expect_response(
        lambda r: "/budget/update/" in r.url and r.request.method == "POST",
        timeout=5000,
    ):
        page.locator("#budget-tbody tr.total-row td").first.click()

    # Verify
    page.goto(f"{flask_server}/f/test_finances/budget")
    assert page.locator("#budget-tbody").get_by_text("Monthly Salary").is_visible()


def test_budget_add_income(page, flask_server):
    """Adding a new income entry via the add row."""
    page.goto(f"{flask_server}/f/test_finances/budget")
    enable_edit_mode(page)

    add_row = page.locator("#budget-add-row")
    # Kind defaults to income
    add_row.locator("input[name='description']").fill("Side Gig")
    add_row.locator("input[name='amount']").fill("200")

    add_row.locator("button[title='Add']").click()
    page.wait_for_timeout(500)

    # Should see the new entry after page refresh (HX-Refresh)
    page.goto(f"{flask_server}/f/test_finances/budget")
    assert page.locator("#budget-tbody").get_by_text("Side Gig").is_visible()


def test_budget_add_expense(page, flask_server):
    """Adding a new expense entry via the add row."""
    page.goto(f"{flask_server}/f/test_finances/budget")
    enable_edit_mode(page)

    add_row = page.locator("#budget-add-row")
    add_row.locator("select[name='kind']").select_option("expense")
    page.wait_for_timeout(200)

    add_row.locator("input[name='description']").fill("Internet")
    add_row.locator("input[name='amount']").fill("80")

    add_row.locator("button[title='Add']").click()
    page.wait_for_timeout(500)

    page.goto(f"{flask_server}/f/test_finances/budget")
    assert page.locator("#budget-tbody").get_by_text("Internet").is_visible()


def test_budget_kind_selector_filters_types(page, flask_server):
    """Switching kind in add row filters the type dropdown options."""
    page.goto(f"{flask_server}/f/test_finances/budget")
    enable_edit_mode(page)

    add_row = page.locator("#budget-add-row")
    type_select = add_row.locator("select[name='type']")

    # When kind=income, income types should be visible
    add_row.locator("select[name='kind']").select_option("income")
    page.wait_for_timeout(200)

    # salary option should be visible
    salary_opt = type_select.locator("option[value='salary']")
    assert salary_opt.count() > 0

    # Switch to expense
    add_row.locator("select[name='kind']").select_option("expense")
    page.wait_for_timeout(200)

    # housing option should be visible
    housing_opt = type_select.locator("option[value='housing']")
    assert housing_opt.count() > 0


def test_budget_delete(page, flask_server):
    """Deleting a budget entry removes it."""
    page.goto(f"{flask_server}/f/test_finances/budget")
    enable_edit_mode(page)

    # Find the Salary row and click delete
    salary_row = page.locator("#budget-tbody tr:has-text('Salary')").first
    salary_row.locator("button[title='Delete']").click()

    # Confirm delete
    confirm_btn = salary_row.locator("button[title='Confirm delete']")
    confirm_btn.wait_for(state="visible")
    confirm_btn.click()
    page.wait_for_timeout(500)

    # Verify - reload page (exclude add row, whose type select contains "salary" as an option)
    page.goto(f"{flask_server}/f/test_finances/budget")
    assert (
        page.locator("#budget-tbody tr:not([data-add-row])")
        .filter(has_text="Salary")
        .count()
        == 0
    )


def test_budget_filter_by_kind(page, flask_server):
    """Filtering by kind shows only matching entries."""
    page.goto(f"{flask_server}/f/test_finances/budget")

    # Open Kind filter
    page.locator(".sm\\:flex button.filter-dropdown-trigger:has-text('Kind')").click()

    # Check income only
    page.locator(".sm\\:flex input[name='include_kind'][value='income']").click()
    page.wait_for_url("**/budget**include_kind**")

    tbody = page.locator("#budget-tbody")
    # Should show income items but not expenses
    assert tbody.get_by_text("Salary", exact=True).first.is_visible()
    # Rent is an expense - should not be shown in table rows
    assert tbody.locator("tr").filter(has_text="Rent").count() == 0


def test_budget_filter_by_kind_mobile(page, flask_server):
    """On a mobile viewport, the mobile filter panel toggle opens filters that work."""
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(f"{flask_server}/f/test_finances/budget")

    # Desktop bar is hidden; mobile trigger row is visible
    assert page.locator(".sm\\:hidden").first.is_visible()
    assert not page.locator(".sm\\:flex").first.is_visible()

    # Expand the mobile filter panel
    page.locator(".sm\\:hidden > button[type=button]").click()

    # Open the Kind dropdown inside the mobile panel
    page.locator(".sm\\:hidden .filter-dropdown-trigger:has-text('Kind')").click()

    # Select income
    page.locator(".sm\\:hidden input[name='include_kind'][value='income']").click()
    page.wait_for_url("**/budget**include_kind**")

    # Active count badge should appear in the mobile trigger row
    assert page.locator(".sm\\:hidden .tabular-nums").is_visible()

    # Correct rows shown
    tbody = page.locator("#budget-tbody")
    assert tbody.get_by_text("Salary", exact=True).first.is_visible()
    assert tbody.locator("tr").filter(has_text="Rent").count() == 0


def test_budget_sort(page, flask_server):
    """Clicking a column header sorts the budget table."""
    page.goto(f"{flask_server}/f/test_finances/budget")

    # Click on sortable header
    page.locator(".sortable-th").first.click()

    indicator = page.locator(".sort-indicator")
    has_indicator = False
    for i in range(indicator.count()):
        if indicator.nth(i).text_content().strip():
            has_indicator = True
            break
    assert has_indicator


def test_budget_monthly_and_annual_columns_visible(page, flask_server):
    """Both Monthly and Annual columns are always visible in the budget table."""
    page.goto(f"{flask_server}/f/test_finances/budget")

    headers = page.locator("table thead th")
    header_texts = headers.all_inner_texts()
    assert "Monthly" in header_texts
    assert "Annual" in header_texts


def test_budget_when_one_time_date_picker(page, flask_server):
    """Clicking 'When' on a one_time row shows a date input."""
    page.goto(f"{flask_server}/f/test_finances/budget")
    enable_edit_mode(page)

    # Find the Tax Refund row (one_time) and click its When cell (col index 5)
    refund_row = page.locator("#budget-tbody tr:has-text('Tax Refund')").first
    when_cell = refund_row.locator("td").nth(5)
    when_cell.click()

    # Should show a text input for the date value
    date_input = page.locator("#budget-tbody input[name='value']")
    date_input.wait_for(state="visible")
    assert date_input.is_visible()


def test_budget_when_one_time_sets_date(page, flask_server):
    """Setting a date on a one_time entry via the date picker saves it."""
    page.goto(f"{flask_server}/f/test_finances/budget")
    enable_edit_mode(page)

    refund_row = page.locator("#budget-tbody tr:has-text('Tax Refund')").first
    when_cell = refund_row.locator("td").nth(5)
    when_cell.click()

    date_input = page.locator("#budget-tbody input[name='value']")
    date_input.wait_for(state="visible")

    # Explicitly click the input to register focus with Playwright's CDP layer.
    date_input.click()
    date_input.fill("2026-02-15")

    # Click total row to blur; wait for the HTMX POST response before moving on
    with page.expect_response(
        lambda r: "/budget/update/" in r.url and r.request.method == "POST",
        timeout=5000,
    ):
        page.locator("#budget-tbody tr.total-row td").first.click()

    # Reload and verify the date is displayed
    page.goto(f"{flask_server}/f/test_finances/budget")
    refund_row = page.locator("#budget-tbody tr:has-text('Tax Refund')").first
    assert refund_row.get_by_text("2026-02-15").is_visible()


def test_budget_when_monthly_day_edit(page, flask_server):
    """Clicking 'When' on a non-continuous monthly row shows compound editor."""
    page.goto(f"{flask_server}/f/test_finances/budget")
    enable_edit_mode(page)

    # Find the Salary row (monthly, dayOfMonth=1) and click its When cell
    salary_row = page.locator("#budget-tbody tr:has-text('Salary')").first
    when_cell = salary_row.locator("td").nth(5)
    when_cell.click()

    # Should show compound editor with dayOfMonth input (no continuous for income)
    editing_cell = salary_row.locator("td.cell-editing")
    day_input = editing_cell.locator("input[name='dayOfMonth']")
    day_input.wait_for(state="visible")
    assert day_input.input_value() == "1"
    assert not editing_cell.locator("input[name='continuous_cb']").is_visible()

    # Change to day 5
    day_input.fill("5")
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)

    # Verify display shows 5th
    page.goto(f"{flask_server}/f/test_finances/budget")
    salary_row = page.locator("#budget-tbody tr:has-text('Salary')").first
    assert salary_row.get_by_text("5th").is_visible()


def test_budget_when_continuous_toggle_off(page, flask_server):
    """Unchecking continuous on a monthly entry and setting a day saves correctly."""
    page.goto(f"{flask_server}/f/test_finances/budget")
    enable_edit_mode(page)

    # Find the Groceries row (monthly, continuous=true) and click its When cell
    groceries_row = page.locator("#budget-tbody tr:has-text('Groceries')").first
    when_cell = groceries_row.locator("td").nth(5)
    when_cell.click()

    # Should show compound editor with checkbox checked
    editing_cell = groceries_row.locator("td.cell-editing")
    cont_cb = editing_cell.locator("input[name='continuous_cb']")
    cont_cb.wait_for(state="visible")
    assert cont_cb.is_checked()

    # Day input should be disabled
    day_input = editing_cell.locator("input[name='dayOfMonth']")
    assert day_input.is_disabled()

    # Uncheck continuous
    cont_cb.click()
    page.wait_for_timeout(200)

    # Day input should now be enabled
    assert not day_input.is_disabled()

    # Enter day 5
    day_input.fill("5")

    # Blur to save (focusout triggers save)
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)

    # Reload and verify display changed from "continuous" to "5th"
    page.goto(f"{flask_server}/f/test_finances/budget")
    groceries_row = page.locator("#budget-tbody tr:has-text('Groceries')").first
    assert groceries_row.get_by_text("5th").is_visible()


def test_budget_when_continuous_toggle_on(page, flask_server):
    """Checking continuous on a monthly expense entry saves it correctly."""
    page.goto(f"{flask_server}/f/test_finances/budget")
    enable_edit_mode(page)

    # Find the Rent row (monthly expense, dayOfMonth=1, no continuous) and click its When cell
    rent_row = page.locator("#budget-row-3")
    when_cell = rent_row.locator("td").nth(5)
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
    page.goto(f"{flask_server}/f/test_finances/budget")
    rent_row = page.locator("#budget-row-3")
    assert rent_row.get_by_text("continuous").is_visible()


def test_budget_when_annual_compound_edit(page, flask_server):
    """Editing the 'When' for an annual entry via compound editor saves month+day."""
    page.goto(f"{flask_server}/f/test_finances/budget")
    enable_edit_mode(page)

    # Find the Car Insurance row (annual, month=3, dayOfMonth=15) and click its When cell
    row = page.locator("#budget-tbody tr:has-text('Car Insurance')").first
    when_cell = row.locator("td").nth(5)
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
    page.goto(f"{flask_server}/f/test_finances/budget")
    row = page.locator("#budget-tbody tr:has-text('Car Insurance')").first
    assert row.get_by_text("Apr 10th").is_visible()
