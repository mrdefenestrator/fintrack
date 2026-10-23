"""E2E: resolving possible duplicates in the staging review and on the
Transactions sheet, and the scan button (issue #82).

Shares one module-scoped ``duplicates_server``; tests run in order.
"""

import pytest

pytestmark = pytest.mark.e2e


def _batch(page, server):
    base, import_id = server
    page.goto(f"{base}/s/ledger/import")
    return page.locator(f"#batch-{import_id}")


def test_flagged_rows_show_the_original_and_why(page, duplicates_server):
    batch = _batch(page, duplicates_server)
    details = batch.locator(".dup-detail")
    assert details.count() == 2
    uber = details.filter(has_text="UBER *TRIP HELP.UBER.COM")
    assert uber.locator("text=Possible duplicate of").is_visible()
    assert uber.locator("text=1 day apart").is_visible()
    assert "first.qfx" in uber.inner_text()


def test_mark_duplicate_then_undo(page, duplicates_server):
    batch = _batch(page, duplicates_server)
    uber = batch.locator(".dup-detail").filter(has_text="UBER *TRIP")
    uber.locator(".dup-action", has_text="Duplicate").first.click()
    uber = batch.locator(".dup-detail").filter(has_text="UBER *TRIP")
    uber.locator("text=Marked duplicate of").wait_for()

    uber.locator(".dup-action", has_text="Undo").click()
    batch.locator(".dup-detail").filter(has_text="UBER *TRIP").locator(
        "text=Possible duplicate of"
    ).wait_for()


def test_resolve_then_confirm_hides_only_the_copy(page, duplicates_server):
    base, import_id = duplicates_server
    batch = _batch(page, duplicates_server)
    batch.locator(".dup-detail").filter(has_text="UBER *TRIP").locator(
        ".dup-action", has_text="Duplicate"
    ).first.click()
    batch.locator("text=Marked duplicate of").wait_for()
    netflix = batch.locator(".dup-detail").filter(has_text="NETFLIX.COM")
    netflix.locator(".dup-action", has_text="Not duplicate").click()
    batch.locator("text=Kept — not a duplicate of").wait_for()

    page.click(f"button[hx-post$='/import/{import_id}/confirm']")
    page.locator("text=No pending imports.").wait_for()

    page.goto(f"{base}/s/ledger/transactions?all_months=true")
    rows = page.locator("tbody tr[id^='txn-']")
    # Original Uber kept, copy hidden; both Netflix rows kept; Shell new.
    assert rows.count() == 4
    assert rows.filter(has_text="UBER").count() == 1
    assert rows.filter(has_text="netflix").count() == 2  # has_text ignores case


def test_marked_duplicate_filter_and_undo_on_sheet(page, duplicates_server):
    base, _ = duplicates_server
    page.goto(f"{base}/s/ledger/transactions?all_months=true&status=duplicate")
    row = page.locator("tbody tr[data-dup-status]")
    assert row.count() == 1
    assert "Duplicate of 2026-04-01 · UBER *TRIP HELP.UBER.COM" in row.inner_text()

    row.locator(".dup-action", has_text="Undo").click()
    page.locator("tbody tr[data-dup-status='flagged']").wait_for()

    page.goto(f"{base}/s/ledger/transactions?all_months=true&status=possible_duplicate")
    row = page.locator("tbody tr[data-dup-status='flagged']")
    assert row.count() == 1
    row.locator(".dup-action", has_text="Duplicate").first.click()
    page.locator("tbody tr[data-dup-status='duplicate']").wait_for()


def test_status_filter_offers_duplicate_views(page, duplicates_server):
    base, _ = duplicates_server
    page.goto(f"{base}/s/ledger/transactions?all_months=true")
    html = page.content()
    assert "Possible duplicate" in html and "Marked duplicate" in html


def test_scan_button_reports_nothing_new(page, duplicates_server):
    base, _ = duplicates_server
    page.goto(f"{base}/s/ledger/import")
    page.click("#scan-duplicates")
    page.locator(
        "#scan-result", has_text="No new possible duplicates found."
    ).wait_for()
