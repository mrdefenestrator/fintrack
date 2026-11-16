"""Holdings delete (#157): confirming the delete of a holding that is still
linked (a budget entry's account, a loan's secured asset) used to do nothing
visible — the server refused with an empty 422. It now shows why, in view."""

import pytest
from playwright.sync_api import expect

from tests.e2e.controls import open_row_menu

pytestmark = pytest.mark.e2e


def _row(page, band, name):
    # Scoped to the band: a loan's Linked column names its asset too.
    return page.locator(f"#holdings-{band} tr[data-reorder-index]").filter(
        has_text=name
    )


def _confirm_delete(page, row):
    open_row_menu(row)
    page.locator(".row-menu:popover-open [data-row-delete]").click()
    row.locator(".row-confirm-btn--yes").click()


@pytest.mark.parametrize(
    "band, name, linked_from",
    [
        ("cash", "Main Checking", "budget entry"),
        ("asset", "Primary Residence", "Mortgage (secured asset)"),
    ],
)
def test_blocked_delete_says_why(page, flask_server, band, name, linked_from):
    page.set_viewport_size({"width": 1280, "height": 500})  # sheet scrolls
    page.goto(f"{flask_server}/s/test_finances/holdings?edit=1")
    row = _row(page, band, name)
    row.scroll_into_view_if_needed()
    _confirm_delete(page, row)

    error = page.locator("#holdings-error-row")
    expect(error).to_contain_text(f"Can't delete {name}")
    expect(error).to_contain_text(linked_from)
    expect(error).to_be_in_viewport()
    # Nothing was deleted, and the row is back to its normal actions.
    expect(_row(page, band, name)).to_be_visible()
    expect(page.locator(".row-actions-cell--confirm")).to_have_count(0)
