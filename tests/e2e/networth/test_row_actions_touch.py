"""Row actions on a touch screen (issue #148): the grip and ⋯ widen to a
finger-sized target (the row height stays put), the menu's items are tall
enough to tap, and the menu fits the phone's screen."""

import pytest
from playwright.sync_api import expect

from tests.e2e.controls import open_row_menu

pytestmark = pytest.mark.e2e


@pytest.fixture
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "viewport": {"width": 390, "height": 844},
        "has_touch": True,
        "is_mobile": True,
    }


def test_touch_targets(page, flask_server):
    page.goto(f"{flask_server}/s/test_finances/budget?edit=1")
    row = page.locator("tr[id^='budget-row-']").first
    for sel in (".drag-handle", ".row-menu-btn"):
        assert row.locator(sel).bounding_box()["width"] >= 32
    menu = open_row_menu(row)
    for item in menu.get_by_role("menuitem").all():
        assert item.bounding_box()["height"] >= 44
    m = menu.bounding_box()
    assert m["x"] >= 0 and m["x"] + m["width"] <= 390


def test_tap_a_menu_item(page, flask_server):
    page.goto(f"{flask_server}/s/test_finances/budget?edit=1")
    row = page.locator("tr[id^='budget-row-']").first
    row.locator(".row-menu-btn").tap()
    menu = row.locator(".row-menu")
    expect(menu).to_be_visible()
    menu.locator("[data-row-delete]").tap()
    expect(row.locator(".row-actions-cell--confirm")).to_be_visible()
