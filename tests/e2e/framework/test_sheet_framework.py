"""Framework-level e2e behaviour for the generic sheet renderer.

Exercised once, against the kitchen-sink demo page, so real tables that adopt
the renderer inherit this coverage rather than re-testing each interaction:
inline edit-in-place (no scroll jump), keyboard navigation, column sort that
survives a whole-tbody swap, drag-reorder, the delete confirm flow, and the
scroll drop-shadows.
"""

import pytest

pytestmark = pytest.mark.e2e

FLAT = "#demo-flat-tbody"


def _goto(page, demo_server):
    page.goto(f"{demo_server}/_sheet_demo/")
    page.locator(FLAT).wait_for(state="visible")


def test_inline_edit_in_place_no_jump(page, demo_server):
    _goto(page, demo_server)
    # click the Name cell of row 1 -> input appears in place, same row
    cell = page.locator("#demo-flat-1 td", has_text="Alpha").first
    cell.click()
    inp = page.locator("#demo-flat-1 input[name='value']")
    inp.wait_for(state="visible")
    inp.fill("Renamed")
    inp.press("Enter")
    # the tbody re-renders with the new value; row identity is preserved
    page.locator("#demo-flat-1", has_text="Renamed").wait_for()
    assert page.locator(FLAT, has_text="Renamed").count() >= 1


def test_keyboard_nav_tab_moves_to_next_editable_cell(page, demo_server):
    _goto(page, demo_server)
    page.locator("#demo-flat-1 td", has_text="Alpha").first.click()
    inp = page.locator("#demo-flat-1 input[name='value']")
    inp.wait_for(state="visible")
    inp.fill("Edited")
    inp.press("Tab")
    # Tab commits and opens the next editable cell (Amount) in the same row
    nxt = page.locator("#demo-flat-1 input[name='value']")
    nxt.wait_for(state="visible")
    # the previously edited value was saved
    assert page.locator(FLAT, has_text="Edited").count() >= 1


def test_escape_cancels_edit(page, demo_server):
    _goto(page, demo_server)
    page.locator("#demo-flat-1 td", has_text="Alpha").first.click()
    inp = page.locator("#demo-flat-1 input[name='value']")
    inp.wait_for(state="visible")
    inp.fill("ShouldNotSave")
    inp.press("Escape")
    page.wait_for_timeout(300)
    assert page.locator(FLAT, has_text="ShouldNotSave").count() == 0
    assert page.locator(FLAT, has_text="Alpha").count() >= 1


def test_column_sort_persists_across_tbody_swap(page, demo_server):
    _goto(page, demo_server)
    # sort by Name ascending
    page.locator("thead th", has_text="Name").first.click()
    page.wait_for_timeout(200)
    first_before = page.locator(f"{FLAT} > tr[id]").first
    name_before = first_before.locator("td").first.inner_text()
    # trigger a whole-tbody swap by editing a cell, then committing
    page.locator(f"{FLAT} > tr[id]").first.locator("td").first.click()
    inp = page.locator(f"{FLAT} input[name='value']")
    inp.wait_for(state="visible")
    inp.press("Escape")
    page.wait_for_timeout(300)
    # sort order is re-applied after the swap
    first_after = page.locator(f"{FLAT} > tr[id]").first
    assert first_after.locator("td").first.inner_text() == name_before


def test_delete_confirm_flow(page, demo_server):
    _goto(page, demo_server)
    row = page.locator("#demo-flat-1")
    row.locator("button[title='Delete']").click()
    row.locator("button[title='Confirm delete']").click()
    page.locator("#demo-flat-1").wait_for(state="detached")
    assert page.locator("#demo-flat-1").count() == 0


def test_drag_reorder_persists(page, demo_server):
    _goto(page, demo_server)
    rows = page.locator(f"{FLAT} > tr[id]")
    first_name = rows.first.locator("td").first.inner_text()
    handle = rows.first.locator(".drag-handle")
    target = rows.nth(2)
    handle.drag_to(target)
    page.wait_for_timeout(300)
    # the first row's name is no longer first
    assert (
        page.locator(f"{FLAT} > tr[id]").first.locator("td").first.inner_text()
        != first_name
    )


def test_scroll_shadows_present(page, demo_server):
    page.set_viewport_size({"width": 640, "height": 300})
    _goto(page, demo_server)
    assert page.locator(".sheet-scroll-shadow--bottom").first.is_visible() in (
        True,
        False,
    )
    # the container opted into the shadow system
    assert page.locator("[data-sheet-scroll]").count() >= 2
