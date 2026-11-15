"""Row actions (issue #148): one [ grip ][ ⋯ ] actions column on every sheet
that has one, a labeled ⋯ menu that the scroll container can't clip, keyboard
reorder from the grip, and finger-sized targets on a touch screen."""

from urllib.parse import parse_qs

import pytest
from playwright.sync_api import expect

from tests.e2e.controls import open_row_menu
from tests.e2e.networth.test_budget_actual import _add_monthly_series

pytestmark = pytest.mark.e2e

_SHEETS = [
    "holdings?edit=1",
    "budget?edit=1",
    "budget?view=recurring&edit=1",  # with a recurring series seeded
    "categories?edit=1",
]


def _cells(page):
    return page.locator("tr:has(.row-menu-btn) > td.table-actions-cell")


@pytest.mark.parametrize("path", _SHEETS)
def test_same_two_slot_column_everywhere(page, flask_server, _db_path, path):
    """Every sheet's actions column is the same width, and ⋯ sits at the same
    spot in it on every row — whether or not the row has a grip."""
    _add_monthly_series(_db_path)
    page.goto(f"{flask_server}/s/test_finances/{path}")
    cells = _cells(page)
    assert cells.count() > 0
    widths = set()
    offsets = set()
    for i in range(cells.count()):
        cell = cells.nth(i).bounding_box()
        menu = cells.nth(i).locator(".row-menu-btn").bounding_box()
        widths.add(round(cell["width"]))
        offsets.add(round(cell["x"] + cell["width"] - menu["x"]))
    assert len(widths) == 1 and len(offsets) == 1
    # The width doesn't depend on the sheet: two 1.375rem slots + padding.
    assert widths == {50}


def test_menu_opens_beside_its_button(page, flask_server):
    page.goto(f"{flask_server}/s/test_finances/holdings?edit=1")
    row = page.locator("tr[id^='holding-row-']").first
    btn = row.locator(".row-menu-btn")
    menu = open_row_menu(row)
    b, m = btn.bounding_box(), menu.bounding_box()
    assert abs((m["x"] + m["width"]) - (b["x"] + b["width"])) <= 1  # right-aligned
    assert 0 <= m["y"] - (b["y"] + b["height"]) <= 6  # just below
    expect(btn).to_have_attribute("aria-expanded", "true")
    # Focus goes to the first item; Esc closes and hands focus back.
    expect(menu.get_by_role("menuitem").first).to_be_focused()
    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()
    expect(btn).to_be_focused()


def test_menu_by_the_footer_stays_on_screen_and_on_top(page, flask_server):
    """The popover sits in the top layer: by the pinned footer it flips above
    its button when it won't fit below, stays inside the viewport, and nothing
    (footer, scroll container) covers it."""
    page.set_viewport_size({"width": 1200, "height": 420})
    page.goto(f"{flask_server}/s/test_finances/budget?group=none&edit=1")
    footer_top = page.locator("tr.total-row").first.bounding_box()["y"]
    rows = page.locator("tr[id^='budget-row-']")
    row = next(
        rows.nth(i)
        for i in reversed(range(rows.count()))
        if (box := rows.nth(i).bounding_box())
        and box["y"] + box["height"] <= footer_top
    )
    b = row.locator(".row-menu-btn").bounding_box()
    menu = open_row_menu(row)
    m = menu.bounding_box()
    if b["y"] + b["height"] + m["height"] + 12 > 420:
        assert m["y"] + m["height"] <= b["y"]  # flipped above
    else:
        assert m["y"] >= b["y"] + b["height"]
    assert m["y"] >= 0 and m["y"] + m["height"] <= 420
    for item in menu.get_by_role("menuitem").all():
        item.hover(trial=True)  # hit-testable: not covered by sticky chrome


def test_choosing_an_item_closes_the_menu(page, flask_server):
    page.goto(f"{flask_server}/s/test_finances/holdings?edit=1")
    row = page.locator("tr[id^='holding-row-']").first
    menu = open_row_menu(row)
    menu.locator("[data-row-delete]").click()
    expect(menu).to_be_hidden()
    expect(row.locator(".row-actions-cell--confirm")).to_be_visible()
    # ✗ has focus (a stray Enter cancels) and restores the actions.
    expect(row.get_by_role("button", name="Cancel delete")).to_be_focused()
    page.keyboard.press("Enter")
    expect(row.locator(".row-menu-btn")).to_be_focused()


def test_scrolling_the_sheet_closes_the_menu(page, flask_server):
    page.set_viewport_size({"width": 900, "height": 360})
    page.goto(f"{flask_server}/s/test_finances/budget?group=none&edit=1")
    container = page.locator(".table-scroll-container")
    assert container.evaluate("c => c.scrollHeight > c.clientHeight")
    menu = open_row_menu(page.locator("tr[id^='budget-row-']").first)
    container.evaluate("c => c.scrollBy(0, 40)")
    expect(menu).to_be_hidden()


def test_keyboard_reorder_from_the_grip(page, flask_server):
    posted = []

    def capture(route):
        posted.append(route.request.post_data)
        route.fulfill(status=204, body="")

    page.route("**/holdings/reorder**", capture)
    page.goto(f"{flask_server}/s/test_finances/holdings?edit=1")
    bands = page.locator("tbody[data-reorder-url]")
    band = next(
        bands.nth(i)
        for i in range(bands.count())
        if bands.nth(i).locator("tr[data-reorder-index]").count() >= 2
    )
    rows = band.locator("tr[data-reorder-index]")
    first, second = rows.nth(0).get_attribute("id"), rows.nth(1).get_attribute("id")
    rows.nth(0).locator(".drag-handle").focus()
    page.keyboard.press("ArrowDown")
    expect(rows.nth(0)).to_have_id(second)
    expect(rows.nth(1)).to_have_id(first)
    expect(page.locator(f"#{first} .drag-handle")).to_be_focused()
    # Back up again: the second move is relative to the first (renumbered).
    page.keyboard.press("ArrowUp")
    expect(rows.nth(0)).to_have_id(first)
    page.wait_for_timeout(200)
    orders = [parse_qs(d)["order"][0] for d in posted]
    n = rows.count()
    swapped = ",".join(["1", "0"] + [str(i) for i in range(2, n)])
    assert orders == [swapped, swapped]


def test_sorted_band_hides_grip_but_keeps_menu_in_place(page, flask_server):
    page.goto(f"{flask_server}/s/test_finances/holdings?edit=1")
    band = page.locator("tbody[data-reorder-url]").first
    row = band.locator("tr[data-reorder-index]").first
    before = row.locator(".row-menu-btn").bounding_box()["x"]
    band.locator("th[data-sort-key='c2']").click()  # Name
    row = band.locator("tr[data-reorder-index]").first
    expect(row.locator(".drag-handle")).to_be_hidden()
    assert row.locator(".row-menu-btn").bounding_box()["x"] == before
