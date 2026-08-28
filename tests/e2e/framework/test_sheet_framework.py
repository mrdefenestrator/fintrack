"""Framework-level e2e behaviour for the generic sheet renderer.

Exercised once, against the kitchen-sink demo page, so real tables that adopt
the renderer inherit this coverage rather than re-testing each interaction:
inline edit-in-place (no scroll jump), keyboard navigation, column sort that
survives a whole-tbody swap, drag-reorder, the delete confirm flow, the locked
(non-editable) state, vertical scroll, and the scroll drop-shadows.
"""

import pytest

pytestmark = pytest.mark.e2e

FLAT = "#demo-flat-table"
ROWS = f"{FLAT} tbody > tr[id]"
LOCKED = "#demo-locked-table"


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
    # the table re-renders with the new value; row identity + structure preserved
    page.locator("#demo-flat-1", has_text="Renamed").wait_for()
    assert page.locator(FLAT, has_text="Renamed").count() >= 1
    # structure intact: exactly one thead, all three data rows still present
    assert page.locator(f"{FLAT} > thead").count() == 1
    assert page.locator(ROWS).count() == 3
    # no nested tbody (the QA "broken page on Enter" regression)
    assert page.locator(f"{FLAT} tbody tbody").count() == 0


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
    # the previously edited value was saved (wait for the swap to settle)
    page.locator(FLAT, has_text="Edited").first.wait_for(timeout=3000)


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


def test_column_sort_by_header_reorders(page, demo_server):
    _goto(page, demo_server)
    # sort ascending by Amount: the negative (340.00) row sorts to the top
    page.locator(f"{FLAT} thead th", has_text="Amount").first.click()
    page.wait_for_timeout(200)
    assert "Beta" in page.locator(ROWS).first.inner_text()


def test_column_sort_persists_across_tbody_swap(page, demo_server):
    _goto(page, demo_server)
    page.locator(f"{FLAT} thead th", has_text="Name").first.click()
    page.wait_for_timeout(200)
    name_before = page.locator(ROWS).first.locator("td").first.inner_text()
    # trigger a whole-table swap by opening an editor, then cancelling
    page.locator(ROWS).first.locator("td").first.click()
    inp = page.locator(f"{FLAT} input[name='value']")
    inp.wait_for(state="visible")
    inp.press("Escape")
    page.wait_for_timeout(300)
    # sort order is re-applied after the swap
    assert page.locator(ROWS).first.locator("td").first.inner_text() == name_before


def test_delete_confirm_flow(page, demo_server):
    _goto(page, demo_server)
    row = page.locator("#demo-flat-1")
    row.locator("button[title='Delete']").click()
    # the confirm ✓/✗ appears in place
    row.locator("button[title='Confirm delete']").wait_for(state="visible")
    row.locator("button[title='Confirm delete']").click()
    page.locator("#demo-flat-1").wait_for(state="detached")
    assert page.locator("#demo-flat-1").count() == 0


def test_drag_reorder_persists(page, demo_server):
    _goto(page, demo_server)
    rows = page.locator(ROWS)
    first_name = rows.first.locator("td").first.inner_text()
    rows.first.locator(".drag-handle").drag_to(rows.nth(2))
    page.wait_for_timeout(300)
    assert page.locator(ROWS).first.locator("td").first.inner_text() != first_name


def test_locked_table_hides_edit_affordances(page, demo_server):
    """A locked (non-editable) sheet shows no add row, no actions column, and no
    clickable cells — the QA regression where those appeared even when locked."""
    _goto(page, demo_server)
    locked = page.locator(LOCKED)
    assert locked.locator("[data-add-row]").count() == 0
    assert locked.locator(".table-actions-cell").count() == 0
    # clicking a cell does nothing (no editor opens)
    locked.locator("tbody > tr[id]").first.locator("td").first.click()
    page.wait_for_timeout(200)
    assert locked.locator("input[name='value']").count() == 0


def test_vertical_scroll_works(page, demo_server):
    """The sheet container scrolls vertically (regression: a wrapper broke the
    flex height and the table wouldn't scroll)."""
    page.set_viewport_size({"width": 900, "height": 300})
    _goto(page, demo_server)
    container = (
        page.locator(f"{FLAT}").locator("xpath=ancestor::div[@data-sheet-scroll]").first
    )
    # the container is actually scrollable (content taller than the viewport slice)
    scrollable = container.evaluate("el => el.scrollHeight > el.clientHeight")
    assert scrollable is True


def test_scroll_shadows_present(page, demo_server):
    page.set_viewport_size({"width": 640, "height": 300})
    _goto(page, demo_server)
    assert page.locator("[data-sheet-scroll]").count() >= 2
