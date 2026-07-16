"""File picker e2e tests — select, duplicate, rename, delete files."""

import pytest

pytestmark = pytest.mark.e2e


def test_file_picker_hover_reveals_buttons(page, flask_server):
    """Hovering the active file row reveals rename and duplicate buttons."""
    page.goto(f"{flask_server}/f/test_finances/accounts")
    page.locator("[data-file-picker]").click()
    page.locator("[data-file-picker-dropdown]").wait_for(state="visible")

    page.locator("[data-active-file]").hover()

    rename_btn = page.locator("button[aria-label='Rename']")
    duplicate_btn = page.locator("button[aria-label='Duplicate']")
    rename_btn.wait_for(state="visible")
    assert rename_btn.is_visible()
    assert duplicate_btn.is_visible()


def test_file_picker_shows_active_file(page, flask_server):
    """File picker button is visible and active file is marked in the dropdown."""
    page.goto(f"{flask_server}/f/test_finances/accounts")
    assert page.locator("[data-file-picker]").is_visible()
    page.locator("[data-file-picker]").click()
    page.locator("[data-file-picker-dropdown]").wait_for(state="visible")
    assert page.locator("[data-active-file]").is_visible()


def test_file_picker_lists_files(page, flask_server):
    """Opening the file picker shows available files."""
    page.goto(f"{flask_server}/f/test_finances/accounts")
    page.locator("[data-file-picker]").click()
    dropdown = page.locator("[data-file-picker-dropdown]")
    dropdown.wait_for(state="visible")
    # Should mark the active file with data-active-file
    assert dropdown.locator("[data-active-file]").count() >= 1


def test_file_picker_create_new_file(page, flask_server):
    """Creating a new file via the picker navigates back to a valid page."""
    page.goto(f"{flask_server}/f/test_finances/accounts")
    page.locator("[data-file-picker]").click()
    page.locator("[data-file-picker-dropdown]").wait_for(state="visible")

    # Click "New file" button
    page.get_by_text("New file").click()

    # Fill in the name and submit
    name_input = page.locator("input[placeholder='filename']")
    name_input.wait_for(state="visible")
    name_input.fill("e2e-test-new")
    page.get_by_text("Create").click()

    # Should end up on a valid page (not blank /files/new)
    page.wait_for_timeout(500)
    assert "/files/" not in page.url
    assert page.locator("header").is_visible()


def test_file_picker_duplicate_file(page, flask_server):
    """Duplicating a file via the picker navigates back to a valid page."""
    page.goto(f"{flask_server}/f/test_finances/accounts")
    page.locator("[data-file-picker]").click()
    page.locator("[data-file-picker-dropdown]").wait_for(state="visible")

    # Hover the active file row to reveal icon buttons, then click Duplicate
    page.locator("[data-active-file]").hover()
    page.locator("button[aria-label='Duplicate']").click()

    # Fill in the name and submit
    name_input = page.locator("input[placeholder='filename']")
    name_input.wait_for(state="visible")
    name_input.fill("e2e-test-copy")
    page.get_by_text("Create").click()

    # Should end up on a valid page (not blank /files/copy)
    page.wait_for_timeout(500)
    assert "/files/" not in page.url
    assert page.locator("header").is_visible()


def test_file_picker_rename_file(page, flask_server):
    """Renaming a file via the picker navigates back to a valid page."""
    # First create a file to rename
    page.goto(f"{flask_server}/f/test_finances/accounts")
    page.locator("[data-file-picker]").click()
    page.locator("[data-file-picker-dropdown]").wait_for(state="visible")
    page.get_by_text("New file").click()
    name_input = page.locator("input[placeholder='filename']")
    name_input.wait_for(state="visible")
    name_input.fill("e2e-rename-source")
    page.get_by_text("Create").click()
    page.wait_for_timeout(500)

    # Now rename it
    page.locator("[data-file-picker]").click()
    page.locator("[data-file-picker-dropdown]").wait_for(state="visible")
    page.locator("[data-active-file]").hover()
    page.locator("button[aria-label='Rename']").click()
    name_input = page.locator("input[placeholder='filename']")
    name_input.wait_for(state="visible")
    name_input.fill("e2e-rename-dest")
    page.get_by_text("Save").click()

    # Should end up on a valid page (not blank /files/rename)
    page.wait_for_timeout(500)
    assert "/files/" not in page.url
    assert page.locator("header").is_visible()


def test_file_picker_select_file(page, flask_server):
    """Selecting a different file reloads the page with that file's data."""
    # First create a second file so we have something to switch to
    page.goto(f"{flask_server}/f/test_finances/accounts")
    page.locator("[data-file-picker]").click()
    page.locator("[data-file-picker-dropdown]").wait_for(state="visible")
    page.get_by_text("New file").click()
    name_input = page.locator("input[placeholder='filename']")
    name_input.wait_for(state="visible")
    name_input.fill("e2e-select-target")
    page.get_by_text("Create").click()
    page.wait_for_timeout(500)

    # Switch back to the original file
    page.locator("[data-file-picker]").click()
    page.locator("[data-file-picker-dropdown]").wait_for(state="visible")
    # Click the first non-active file (the test_finances file)
    page.locator("[data-file-picker-dropdown] a").first.click()

    # Should end up on a valid page (not blank /files/select)
    page.wait_for_timeout(500)
    assert "/files/" not in page.url
    assert page.locator("header").is_visible()


def test_file_picker_hover_reveals_delete_button(page, flask_server):
    """Hovering a non-active file row reveals its delete button."""
    # Create a second file so test_finances becomes non-active after switching
    page.goto(f"{flask_server}/f/test_finances/accounts")
    page.locator("[data-file-picker]").click()
    page.locator("[data-file-picker-dropdown]").wait_for(state="visible")
    page.get_by_text("New file").click()
    name_input = page.locator("input[placeholder='filename']")
    name_input.wait_for(state="visible")
    name_input.fill("e2e-hover-delete")
    page.get_by_text("Create").click()
    page.wait_for_timeout(500)

    # Now test_finances is a non-active row — hover it and check delete button
    page.locator("[data-file-picker]").click()
    page.locator("[data-file-picker-dropdown]").wait_for(state="visible")
    non_active_link = page.locator("[data-file-picker-dropdown] a").first
    non_active_link.hover()

    delete_btn = page.locator("button[title='Delete test_finances.yaml']")
    delete_btn.wait_for(state="visible")
    assert delete_btn.is_visible()


def test_file_picker_delete_non_active(page, flask_server):
    """Deleting a non-active file stays on a valid page."""
    # First create a file to delete
    page.goto(f"{flask_server}/f/test_finances/accounts")
    page.locator("[data-file-picker]").click()
    page.locator("[data-file-picker-dropdown]").wait_for(state="visible")
    page.get_by_text("New file").click()
    name_input = page.locator("input[placeholder='filename']")
    name_input.wait_for(state="visible")
    name_input.fill("e2e-to-delete")
    page.get_by_text("Create").click()
    page.wait_for_timeout(500)

    # Switch to original file so we can delete the new one
    page.locator("[data-file-picker]").click()
    page.locator("[data-file-picker-dropdown]").wait_for(state="visible")
    page.locator("[data-file-picker-dropdown] a").first.click()
    page.wait_for_timeout(500)

    # Now delete the created file
    page.locator("[data-file-picker]").click()
    page.locator("[data-file-picker-dropdown]").wait_for(state="visible")

    # Hover the row to reveal the delete button, then click it
    non_active_link = page.locator("[data-file-picker-dropdown] a").filter(
        has_text="e2e-to-delete"
    )
    non_active_link.hover()
    delete_btn = page.locator("button[title='Delete e2e-to-delete.yaml']")
    delete_btn.wait_for(state="visible")
    # Accept the confirm dialog
    page.on("dialog", lambda d: d.accept())
    delete_btn.click()

    # Should end up on a valid page (not blank /files/delete)
    page.wait_for_timeout(500)
    assert "/files/" not in page.url
    assert page.locator("header").is_visible()
