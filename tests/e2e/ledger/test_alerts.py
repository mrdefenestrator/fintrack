"""E2E: unusual spending alerts (issue #101) — the import summary, the
Overview "Spending alerts" card, Transactions markers, the Flagged filter,
and Dismiss / Undo.

Shares one module-scoped ``alerts_server``; tests run in order.
"""

import pytest

pytestmark = pytest.mark.e2e


def test_confirming_an_import_says_what_to_look_at(page, alerts_server):
    base, import_id = alerts_server
    page.goto(f"{base}/s/ledger/import")
    page.click(f"button[hx-post$='/import/{import_id}/confirm']")
    notice = page.locator("#import-alerts")
    notice.wait_for()
    assert "2 things to look at in this import" in notice.inner_text()


def test_overview_card_opens_the_flagged_rows(page, alerts_server):
    base, _ = alerts_server
    page.goto(f"{base}/s/ledger")
    card = page.locator("[data-overview-card='spending_alerts']")
    text = card.inner_text()
    assert "Large charge: HARDWARE $200" in text
    assert "New merchant: STRANGER $80" in text

    card.click()
    page.wait_for_url("**/transactions?all_months=true&status=flagged")
    rows = page.locator("tbody tr[data-alert]")
    assert rows.count() == 2
    assert page.locator("tbody tr[id^='txn-row-']").count() == 2  # nothing else


def test_dismiss_then_undo_on_the_sheet(page, alerts_server):
    base, _ = alerts_server
    page.goto(f"{base}/s/ledger/transactions?all_months=true&status=flagged")
    row = page.locator("tbody tr[data-alert='large_charge']")
    assert "Large charge · usually $50" in row.inner_text()
    row_id = row.get_attribute("id")

    row.locator(".alert-action", has_text="Dismiss").click()
    page.locator(f"#{row_id} >> text=Alert dismissed").wait_for()
    page.locator(f"#{row_id} .alert-action", has_text="Undo").click()
    page.locator(f"#{row_id}[data-alert='large_charge']").wait_for()

    page.locator(f"#{row_id} .alert-action", has_text="Dismiss").click()
    page.locator(f"#{row_id} >> text=Alert dismissed").wait_for()
    page.reload()
    assert page.locator(f"#{row_id}").count() == 0  # gone from Flagged

    page.goto(f"{base}/s/ledger")
    text = page.locator("[data-overview-card='spending_alerts']").inner_text()
    assert "HARDWARE" not in text
    assert "STRANGER" in text
