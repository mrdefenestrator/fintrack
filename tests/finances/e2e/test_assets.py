"""Assets table e2e tests — CRUD, filters, kind toggle."""

import pytest

from tests.e2e.conftest import enable_edit_mode

pytestmark = pytest.mark.e2e


def test_assets_view_shows_data(page, flask_server):
    """Assets page shows the test fixture data rows."""
    page.goto(f"{flask_server}/f/test_finances/assets")
    tbody = page.locator("#assets-tbody")
    assert tbody.get_by_text("Primary Residence", exact=True).first.is_visible()
    assert tbody.get_by_text("Savings Bonds", exact=True).first.is_visible()
    assert tbody.get_by_text("Mortgage", exact=True).first.is_visible()


def test_assets_shows_total_row(page, flask_server):
    """Assets page shows a total row."""
    page.goto(f"{flask_server}/f/test_finances/assets")
    total_cell = page.locator("#assets-tbody td:has-text('Total')")
    assert total_cell.count() >= 1


def test_assets_click_to_edit(page, flask_server):
    """Clicking a cell in edit mode opens an input."""
    page.goto(f"{flask_server}/f/test_finances/assets")
    enable_edit_mode(page)

    # Click on Primary Residence name cell
    res_row = page.locator("#assets-tbody tr:has-text('Primary Residence')").first
    # Name is the third cell (index 2)
    name_cell = res_row.locator("td").nth(2)
    name_cell.click()

    # The edit input has autofocus attribute, distinguishing it from add-row inputs
    input_el = page.locator("#assets-tbody input[name='value'][autofocus]")
    input_el.wait_for(state="visible")
    assert input_el.input_value() == "Primary Residence"


def test_assets_update_cell(page, flask_server):
    """Editing an asset cell value saves it."""
    page.goto(f"{flask_server}/f/test_finances/assets")
    enable_edit_mode(page)

    res_row = page.locator("#assets-tbody tr:has-text('Primary Residence')").first
    name_cell = res_row.locator("td").nth(2)
    name_cell.click()

    input_el = page.locator("#assets-tbody input[name='value'][autofocus]")
    input_el.wait_for(state="visible")

    # Explicitly click the input to register focus with Playwright's CDP layer.
    input_el.click()
    input_el.fill("My Home")

    # Click total row to blur; wait for the HTMX POST response before moving on
    with page.expect_response(
        lambda r: "/assets/update/" in r.url and r.request.method == "POST",
        timeout=5000,
    ):
        page.locator("#assets-tbody tr.total-row td").first.click()

    page.goto(f"{flask_server}/f/test_finances/assets")
    # "My Home" appears in both asset name and debt's assetRef cell; scope to asset row
    asset_row = page.locator("#assets-tbody tr").filter(has_text="Asset").first
    assert asset_row.get_by_text("My Home").first.is_visible()


def test_assets_add_asset(page, flask_server):
    """Adding a new asset via the add row."""
    page.goto(f"{flask_server}/f/test_finances/assets")
    enable_edit_mode(page)

    add_row = page.locator("#assets-add-row")
    # Kind defaults to asset
    add_row.locator("input[name='name']").fill("Bitcoin")
    add_row.locator("input[name='value']").fill("45000")

    add_row.locator("button[title='Add']").click()
    page.wait_for_timeout(500)

    page.goto(f"{flask_server}/f/test_finances/assets")
    assert page.locator("#assets-tbody").get_by_text("Bitcoin").is_visible()


def test_assets_add_debt(page, flask_server):
    """Adding a new debt via the add row."""
    page.goto(f"{flask_server}/f/test_finances/assets")
    enable_edit_mode(page)

    add_row = page.locator("#assets-add-row")
    add_row.locator("select[name='kind']").select_option("debt")
    page.wait_for_timeout(200)

    add_row.locator("input[name='name']").fill("Car Loan")
    add_row.locator("input[name='balance']").fill("15000")

    add_row.locator("button[title='Add']").click()
    page.wait_for_timeout(500)

    page.goto(f"{flask_server}/f/test_finances/assets")
    assert page.locator("#assets-tbody").get_by_text("Car Loan").is_visible()


def test_assets_kind_toggle_fields(page, flask_server):
    """Switching kind in add row toggles field editability.

    The add row uses Alpine.js x-effect to rename the value/balance input
    (name='value' for asset, name='balance' for debt). The interestRate input
    uses :disabled binding and is disabled when kind=asset.
    """
    page.goto(f"{flask_server}/f/test_finances/assets")
    enable_edit_mode(page)

    add_row = page.locator("#assets-add-row")

    # Default is asset: value input present and enabled, interestRate disabled
    value_input = add_row.locator("input[name='value']")
    interest_input = add_row.locator("input[name='interestRate']")
    assert not value_input.is_disabled()
    assert interest_input.is_disabled()

    # Switch to debt
    add_row.locator("select[name='kind']").select_option("debt")
    page.wait_for_timeout(200)

    # Alpine renames the input from 'value' to 'balance'; interestRate becomes enabled
    balance_input = add_row.locator("input[name='balance']")
    assert not balance_input.is_disabled()
    assert not interest_input.is_disabled()


def test_assets_delete(page, flask_server):
    """Deleting an asset removes it."""
    page.goto(f"{flask_server}/f/test_finances/assets")
    enable_edit_mode(page)

    # Delete Savings Bonds (no debt references)
    bonds_row = page.locator("#assets-tbody tr:has-text('Savings Bonds')").first
    assert bonds_row.is_visible()

    bonds_row.locator("button[title='Delete']").click()

    # Confirm
    confirm_btn = bonds_row.locator("button[title='Confirm delete']")
    confirm_btn.wait_for(state="visible")
    confirm_btn.click()
    page.wait_for_timeout(500)

    page.goto(f"{flask_server}/f/test_finances/assets")
    assert (
        page.locator("#assets-tbody tr").filter(has_text="Savings Bonds").count() == 0
    )


def test_assets_filter_by_kind(page, flask_server):
    """Filtering by kind shows only matching entries."""
    page.goto(f"{flask_server}/f/test_finances/assets")

    page.locator(".sm\\:flex button.filter-dropdown-trigger:has-text('Kind')").click()
    page.locator(".sm\\:flex input[name='include_kind'][value='debt']").click()
    page.wait_for_url("**/assets**include_kind**")

    tbody = page.locator("#assets-tbody")
    assert tbody.get_by_text("Mortgage", exact=True).first.is_visible()
    assert tbody.locator("tr").filter(has_text="Primary Residence").count() == 0


def test_assets_filter_by_kind_mobile(page, flask_server):
    """On a mobile viewport, the mobile filter panel toggle opens filters that work."""
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(f"{flask_server}/f/test_finances/assets")

    # Desktop bar is hidden; mobile trigger row is visible
    assert page.locator(".sm\\:hidden").first.is_visible()
    assert not page.locator(".sm\\:flex").first.is_visible()

    # Expand the mobile filter panel
    page.locator(".sm\\:hidden > button[type=button]").click()

    # Open the Kind dropdown inside the mobile panel
    page.locator(".sm\\:hidden .filter-dropdown-trigger:has-text('Kind')").click()

    # Select debt
    page.locator(".sm\\:hidden input[name='include_kind'][value='debt']").click()
    page.wait_for_url("**/assets**include_kind**")

    # Active count badge should appear in the mobile trigger row
    assert page.locator(".sm\\:hidden .tabular-nums").is_visible()

    # Correct rows shown
    tbody = page.locator("#assets-tbody")
    assert tbody.get_by_text("Mortgage", exact=True).first.is_visible()
    assert tbody.locator("tr").filter(has_text="Primary Residence").count() == 0


def test_assets_sort(page, flask_server):
    """Clicking a column header sorts the assets table."""
    page.goto(f"{flask_server}/f/test_finances/assets")

    page.locator(".sortable-th").first.click()

    indicator = page.locator(".sort-indicator")
    has_indicator = False
    for i in range(indicator.count()):
        if indicator.nth(i).text_content().strip():
            has_indicator = True
            break
    assert has_indicator
