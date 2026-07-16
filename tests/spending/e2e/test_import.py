"""E2E tests for the Import tab.

All tests share a single module-scoped ``import_server`` (empty DB at start).
They run in document order and progressively build state:

  1–3   Empty-DB / account-panel structure
  4     Inline account creation via HTMX form
  5–6   Dropzone and "no pending" empty state
  7–9   File upload → staging review
  10    Confirm clears staging
  11–12 Second upload → reject removes from staging
  13    Duplicate-file detection
  14    OFX detect-account pre-fills institution field
"""

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# 1–3  Empty-DB: create-account form is shown
# ---------------------------------------------------------------------------


def test_import_empty_db_shows_no_accounts_message(page, import_server):
    """With an empty database the account panel shows 'No accounts yet'."""
    page.goto(f"{import_server}/import")
    assert page.locator("#account-panel").is_visible()
    assert page.locator("text=No accounts yet").is_visible()


def test_import_create_form_has_name_field(page, import_server):
    """The inline create-account form has a name input."""
    page.goto(f"{import_server}/import")
    assert page.locator("input[name='acct_name']").is_visible()


def test_import_create_form_has_institution_and_type_fields(page, import_server):
    """The inline create-account form has institution and account-type inputs."""
    page.goto(f"{import_server}/import")
    assert page.locator("input[name='acct_institution']").is_visible()
    assert page.locator("select[name='acct_type']").is_visible()


# ---------------------------------------------------------------------------
# 4  Inline account creation
# ---------------------------------------------------------------------------


def test_import_create_account_via_htmx_form(page, import_server):
    """Filling the create-account form and clicking 'Create Account' replaces
    the panel with an account <select> that has the new account pre-selected."""
    page.goto(f"{import_server}/import")

    page.fill("input[name='acct_name']", "Test Checking")
    page.fill("input[name='acct_institution']", "Test Bank")
    page.select_option("select[name='acct_type']", "checking")

    with page.expect_response(
        lambda r: r.url.endswith("/accounts") and r.status == 200
    ):
        page.click("button:has-text('Create Account')")

    page.wait_for_selector("select[name='account_id']")
    select = page.locator("select[name='account_id']")
    assert select.is_visible()
    options = select.locator("option").all_inner_texts()
    assert any("Test Checking" in o for o in options)


# ---------------------------------------------------------------------------
# 5–6  Dropzone and empty staging state (account now exists)
# ---------------------------------------------------------------------------


def test_import_dropzone_is_visible(page, import_server):
    """Upload dropzone is visible after an account exists."""
    page.goto(f"{import_server}/import")
    assert page.locator("#dropzone").is_visible()


def test_import_no_pending_imports_message(page, import_server):
    """Before any uploads the staging column shows 'No pending imports'."""
    page.goto(f"{import_server}/import")
    assert page.locator("text=No pending imports").is_visible()


# ---------------------------------------------------------------------------
# 7–9  File upload → staging review
# ---------------------------------------------------------------------------


def test_import_upload_ofx_creates_staging_batch(page, import_server, ofx_file):
    """Uploading an OFX file creates a staging batch in the right column."""
    page.goto(f"{import_server}/import")

    # Selecting the file triggers detectAccount() which replaces #account-panel
    with page.expect_response(lambda r: "detect-account" in r.url):
        page.set_input_files("#file-input", str(ofx_file))

    # Re-select account after detect-account replaces the panel (loses prior selection)
    page.select_option("select[name='account_id']", label="Test Checking")

    # Submit the import form (hx-post /import/upload)
    with page.expect_response(lambda r: "/import/upload" in r.url and r.status == 200):
        page.click("button[type='submit']")

    page.wait_for_selector('[id^="batch-"]')


def test_import_staging_shows_content(page, import_server):
    """The staging area is non-empty after an upload."""
    page.goto(f"{import_server}/import")
    content_text = page.locator("#content").inner_text()
    assert "No pending imports" not in content_text


def test_import_staging_review_table_loads(page, import_server):
    """The batch review table loads via HTMX (hx-trigger='load') and shows
    transaction rows for the staged import."""
    page.goto(f"{import_server}/import")
    # The review table inside the batch div is loaded automatically on page load
    page.wait_for_selector('[id^="batch-"] table')
    rows = page.locator('[id^="batch-"] table tbody tr')
    assert rows.count() >= 1


# ---------------------------------------------------------------------------
# 10  Confirm clears staging
# ---------------------------------------------------------------------------


def test_import_confirm_removes_batch_from_staging(page, import_server):
    """Clicking 'Confirm' finalises the import and clears the staging panel."""
    page.goto(f"{import_server}/import")

    with page.expect_response(lambda r: "/confirm" in r.url and r.status == 200):
        page.locator("button:has-text('Confirm')").first.click()

    page.wait_for_selector("text=No pending imports")


# ---------------------------------------------------------------------------
# 11–12  Second upload → reject
# ---------------------------------------------------------------------------


def test_import_second_upload_creates_new_staging(page, import_server, ofx_file_2):
    """Uploading a second, distinct OFX file creates a new staging batch."""
    page.goto(f"{import_server}/import")

    with page.expect_response(lambda r: "detect-account" in r.url):
        page.set_input_files("#file-input", str(ofx_file_2))

    page.select_option("select[name='account_id']", label="Test Checking")

    with page.expect_response(lambda r: "/import/upload" in r.url and r.status == 200):
        page.click("button[type='submit']")

    page.wait_for_selector('[id^="batch-"]')


def test_import_reject_removes_batch_from_staging(page, import_server):
    """Clicking 'Reject' discards the import and removes it from staging."""
    page.goto(f"{import_server}/import")

    with page.expect_response(lambda r: "/reject" in r.url and r.status == 200):
        page.locator("button:has-text('Reject')").first.click()

    page.wait_for_selector("text=No pending imports")


# ---------------------------------------------------------------------------
# 12b  Re-import the rejected file — should be accepted, not blocked
# ---------------------------------------------------------------------------


def test_import_rejected_file_can_be_reimported(page, import_server, ofx_file_2):
    """After rejection, uploading the same file again creates a new staging batch
    instead of showing 'already imported'."""
    page.goto(f"{import_server}/import")

    with page.expect_response(lambda r: "detect-account" in r.url):
        page.set_input_files("#file-input", str(ofx_file_2))

    page.select_option("select[name='account_id']", label="Test Checking")

    with page.expect_response(lambda r: "/import/upload" in r.url and r.status == 200):
        page.click("button[type='submit']")

    content = page.locator("#content").inner_text().lower()
    assert "already imported" not in content
    page.wait_for_selector('[id^="batch-"]')

    # Clean up: reject again so staging is clear for subsequent tests
    with page.expect_response(lambda r: "/reject" in r.url and r.status == 200):
        page.locator("button:has-text('Reject')").first.click()
    page.wait_for_selector("text=No pending imports")


# ---------------------------------------------------------------------------
# 13  Duplicate-file detection
# ---------------------------------------------------------------------------


def test_import_duplicate_file_shows_already_imported(page, import_server, ofx_file):
    """Re-uploading the first OFX file (already confirmed) shows an error
    rather than creating a second staging batch."""
    page.goto(f"{import_server}/import")

    with page.expect_response(lambda r: "detect-account" in r.url):
        page.set_input_files("#file-input", str(ofx_file))

    page.select_option("select[name='account_id']", label="Test Checking")

    with page.expect_response(lambda r: "/import/upload" in r.url and r.status == 200):
        page.click("button[type='submit']")

    content = page.locator("#content").inner_text().lower()
    # Import result shows "already imported" or "0 new, N skipped"
    assert "already imported" in content or "skipped" in content or "0 new" in content


# ---------------------------------------------------------------------------
# 14  OFX detect-account pre-fills the create-account form
# ---------------------------------------------------------------------------


def test_import_ofx_detect_account_prefills_institution(
    page, import_server, ofx_file_with_institution
):
    """Selecting an OFX file with institution metadata pre-fills the account
    panel with the institution name ('Chase')."""
    page.goto(f"{import_server}/import")

    with page.expect_response(lambda r: "detect-account" in r.url and r.status == 200):
        page.set_input_files("#file-input", str(ofx_file_with_institution))

    page.wait_for_selector("#account-panel")
    panel_html = page.locator("#account-panel").inner_html()
    assert "Chase" in panel_html
