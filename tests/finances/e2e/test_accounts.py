"""Accounts table e2e tests — CRUD, filters, sort."""

import pytest

from tests.e2e.conftest import enable_edit_mode

pytestmark = pytest.mark.e2e


def test_accounts_view_shows_data(page, flask_server):
    """Accounts page shows the test fixture data rows."""
    page.goto(f"{flask_server}/f/test_finances/accounts")
    # Should show both accounts from fixture
    assert page.locator("#accounts-tbody").get_by_text("Main Checking").is_visible()
    assert page.locator("#accounts-tbody").get_by_text("Rewards Card").is_visible()


def test_accounts_view_shows_total_row(page, flask_server):
    """Accounts page shows a total row."""
    page.goto(f"{flask_server}/f/test_finances/accounts")
    total_cell = page.locator("#accounts-tbody td:has-text('Total')")
    assert total_cell.count() >= 1


def test_click_to_edit_cell(page, flask_server):
    """Clicking a cell in edit mode opens an input."""
    page.goto(f"{flask_server}/f/test_finances/accounts")
    enable_edit_mode(page)

    # Click on "Main Checking" name cell (Account is the third cell, index 2)
    row = page.locator("#account-row-1")
    name_cell = row.locator("td").nth(2)
    name_cell.click()

    # Should show an input or select
    page.wait_for_selector(
        "#accounts-tbody input[name='value'], #accounts-tbody select[name='value']"
    )
    input_el = page.locator("#accounts-tbody input[name='value']")
    assert input_el.is_visible()
    assert input_el.input_value() == "Main Checking"


def test_update_cell(page, flask_server):
    """Editing a cell value and blurring saves it."""
    page.goto(f"{flask_server}/f/test_finances/accounts")
    enable_edit_mode(page)

    # Click on institution cell for account 1
    row = page.locator("#account-row-1")
    # Institution is the first cell (index 0)
    cells = row.locator("td")
    institution_cell = cells.nth(0)
    institution_cell.click()

    # Wait for input
    input_el = page.locator("#accounts-tbody input[name='value']")
    input_el.wait_for(state="visible")

    # Explicitly click the input to register focus with Playwright's CDP layer
    # (autofocus doesn't count — CDP must track focus for blur to fire properly).
    input_el.click()
    input_el.fill("New Bank Name")

    # Click total row to blur; wait for the HTMX POST response before moving on
    with page.expect_response(
        lambda r: "/accounts/update/" in r.url and r.request.method == "POST",
        timeout=5000,
    ):
        page.locator("#accounts-tbody tr.total-row td").first.click()

    # Verify the value persisted
    page.goto(f"{flask_server}/f/test_finances/accounts")
    row = page.locator("#account-row-1")
    assert row.get_by_text("New Bank Name").is_visible()


def test_add_account_row(page, flask_server):
    """Adding a new account via the add row."""
    page.goto(f"{flask_server}/f/test_finances/accounts")
    enable_edit_mode(page)

    # Fill in the add row
    add_row = page.locator("#accounts-add-row")
    add_row.locator("input[name='name']").fill("Savings Account")
    add_row.locator("input[name='balance']").fill("1000")

    # Click + button
    add_row.locator("button[title='Add']").click()

    # Wait for the new row to appear
    page.wait_for_timeout(500)

    # Should see the new account
    assert page.locator("#accounts-tbody").get_by_text("Savings Account").is_visible()


def test_add_credit_card_account(page, flask_server):
    """Adding a credit card account saves CC-specific fields."""
    page.goto(f"{flask_server}/f/test_finances/accounts")
    enable_edit_mode(page)

    add_row = page.locator("#accounts-add-row")
    add_row.locator("select[name='type']").select_option("credit_card")
    page.wait_for_timeout(200)

    add_row.locator("input[name='name']").fill("Travel Card")
    add_row.locator("input[name='limit']").fill("5000")
    add_row.locator("input[name='available']").fill("4500")

    with page.expect_response(
        lambda r: "/accounts/add" in r.url and r.request.method == "POST",
        timeout=5000,
    ):
        add_row.locator("button[title='Add']").click()

    page.goto(f"{flask_server}/f/test_finances/accounts")
    assert page.locator("#accounts-tbody").get_by_text("Travel Card").is_visible()


def test_add_credit_card_fields_toggle(page, flask_server):
    """Selecting credit_card type enables CC-specific fields in add row."""
    page.goto(f"{flask_server}/f/test_finances/accounts")
    enable_edit_mode(page)

    add_row = page.locator("#accounts-add-row")

    # Initially checking type - balance enabled, limit disabled
    balance_input = add_row.locator("input[name='balance']")
    limit_input = add_row.locator("input[name='limit']")
    assert not balance_input.is_disabled()
    assert limit_input.is_disabled()

    # Switch to credit_card
    add_row.locator("select[name='type']").select_option("credit_card")
    page.wait_for_timeout(200)

    # Now balance disabled, limit/available enabled
    assert balance_input.is_disabled()
    assert not limit_input.is_disabled()
    available_input = add_row.locator("input[name='available']")
    assert not available_input.is_disabled()


def test_delete_account(page, flask_server):
    """Deleting an account removes its row."""
    page.goto(f"{flask_server}/f/test_finances/accounts")
    enable_edit_mode(page)

    # Use account 2 (Rewards Card) — account 1 has income references
    row = page.locator("#account-row-2")
    assert row.is_visible()

    # Click delete icon (the red trash button, last action button)
    row.locator("button[title='Delete']").click()

    # Wait for confirm to appear (✓ / ✗ buttons)
    confirm_btn = row.locator("button[title='Confirm delete']")
    confirm_btn.wait_for(state="visible")
    confirm_btn.click()

    page.wait_for_timeout(500)

    # Verify row is gone
    page.goto(f"{flask_server}/f/test_finances/accounts")
    assert page.locator("#account-row-2").count() == 0


def test_delete_cancel(page, flask_server):
    """Cancelling a delete keeps the row."""
    page.goto(f"{flask_server}/f/test_finances/accounts")
    enable_edit_mode(page)

    row = page.locator("#account-row-1")
    assert row.is_visible()

    # Click delete icon
    row.locator("button[title='Delete']").click()

    # Click cancel (✗ button)
    cancel_btn = row.locator("button[title='Cancel delete']")
    cancel_btn.wait_for(state="visible")
    cancel_btn.click()

    page.wait_for_timeout(300)

    # Row should still exist
    assert page.locator("#account-row-1").is_visible()


def test_filter_by_type(page, flask_server):
    """Filtering by account type shows only matching rows."""
    page.goto(f"{flask_server}/f/test_finances/accounts")

    # Open type filter dropdown
    page.locator(".sm\\:flex button.filter-dropdown-trigger:has-text('Type')").click()

    # Check credit_card (checkbox is inside the dropdown panel next to the trigger)
    page.locator(".sm\\:flex input[name='include_type'][value='credit_card']").click()

    # Wait for page reload
    page.wait_for_url("**/accounts**include_type**")

    # Should show only the credit card
    assert page.locator("#accounts-tbody").get_by_text("Rewards Card").is_visible()
    assert page.locator("#accounts-tbody").get_by_text("Main Checking").count() == 0


def test_filter_by_type_mobile(page, flask_server):
    """On a mobile viewport, the mobile filter panel toggle opens filters that work."""
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(f"{flask_server}/f/test_finances/accounts")

    # Desktop bar is hidden; mobile trigger row is visible
    assert page.locator(".sm\\:hidden").first.is_visible()
    assert not page.locator(".sm\\:flex").first.is_visible()

    # Expand the mobile filter panel
    page.locator(".sm\\:hidden > button[type=button]").click()

    # Filter panel should now be visible — open the Type dropdown inside it
    page.locator(".sm\\:hidden .filter-dropdown-trigger:has-text('Type')").click()

    # Select credit_card
    page.locator(".sm\\:hidden input[name='include_type'][value='credit_card']").click()
    page.wait_for_url("**/accounts**include_type**")

    # Active count badge should appear in the mobile trigger row
    assert page.locator(".sm\\:hidden .tabular-nums").is_visible()

    # Correct rows shown
    assert page.locator("#accounts-tbody").get_by_text("Rewards Card").is_visible()
    assert page.locator("#accounts-tbody").get_by_text("Main Checking").count() == 0


def test_sort_by_column(page, flask_server):
    """Clicking a column header sorts the table."""
    page.goto(f"{flask_server}/f/test_finances/accounts")

    # Click on the first sortable header (Name)
    page.locator(".sortable-th").first.click()

    # Table should have sort indicator
    indicator = page.locator(".sort-indicator")
    # At least one indicator should have content
    has_indicator = False
    for i in range(indicator.count()):
        if indicator.nth(i).text_content().strip():
            has_indicator = True
            break
    assert has_indicator
