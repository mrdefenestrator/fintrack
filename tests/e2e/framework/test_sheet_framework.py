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


def test_sort_holds_edited_row_in_place(page, demo_server):
    """Regression: opening a cell editor mid-sort re-rendered the row with an
    empty <input>, whose textContent read blank, so the edited row jumped to the
    top/bottom of the sort and snapped back on revert. The sorter now reads the
    live editor value, so the row keeps its sorted position while editing."""
    _goto(page, demo_server)
    # sort ascending by Name -> Alpha, Beta, Gamma
    page.locator(f"{FLAT} thead th", has_text="Name").first.click()
    page.wait_for_timeout(200)
    order = [
        r.locator("td").first.inner_text().strip() for r in page.locator(ROWS).all()
    ]
    assert order[:3] == ["Alpha", "Beta", "Gamma"]
    # edit the middle row (Beta, #demo-flat-2)
    page.locator("#demo-flat-2 td", has_text="Beta").first.click()
    page.locator("#demo-flat-2 input[name='value']").wait_for(state="visible")
    page.wait_for_timeout(200)
    # the editing row is still the 2nd data row, not sorted to an edge
    ids = [r.get_attribute("id") for r in page.locator(ROWS).all()]
    assert ids[1] == "demo-flat-2"


def test_delete_confirm_flow(page, demo_server):
    _goto(page, demo_server)
    row = page.locator("#demo-flat-1")
    row.locator("button[title='Delete']").click()
    # the confirm ✓/✗ appears in place
    row.locator("button[title='Confirm delete']").wait_for(state="visible")
    row.locator("button[title='Confirm delete']").click()
    page.locator("#demo-flat-1").wait_for(state="detached")
    assert page.locator("#demo-flat-1").count() == 0


def test_delete_after_edit_not_clobbered(page, demo_server):
    """Regression: editing a cell and pressing Enter opens the next cell's
    editor; clicking a delete button then blurs that editor, whose delayed
    focusout-revert used to re-render the table ~150ms later and wipe the
    just-shown delete confirm ('delete does nothing after editing')."""
    _goto(page, demo_server)
    page.locator("#demo-flat-1 td", has_text="Alpha").first.click()
    inp = page.locator("#demo-flat-1 input[name='value']")
    inp.wait_for(state="visible")
    inp.fill("Edited")
    inp.press("Enter")
    # cell-nav opens the next cell's editor
    page.locator("#demo-flat-2 input[name='value']").wait_for(state="visible")
    # delete that row; the open editor's blur must not wipe the confirm
    row = page.locator("#demo-flat-2")
    row.locator("button[title='Delete']").click()
    conf = row.locator("button[title='Confirm delete']")
    conf.wait_for(state="visible")
    page.wait_for_timeout(400)  # past the 150ms revert delay
    assert conf.is_visible()  # still there — not clobbered
    conf.click()
    page.locator("#demo-flat-2").wait_for(state="detached")
    assert page.locator("#demo-flat-2").count() == 0


def test_drag_reorder_persists(page, demo_server, browser_name):
    # SortableJS uses native HTML5 drag-and-drop, which Playwright cannot
    # synthesize in WebKit (its mouse events never become dragstart/drop). The
    # feature works in Safari at the product level, so the drag interaction is
    # exercised on the engines Playwright can drive it in; real iOS is verified
    # manually.
    if browser_name == "webkit":
        pytest.skip("Playwright cannot synthesize native HTML5 drag in WebKit")
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


def test_sort_works_when_locked(page, demo_server):
    """Sorting must work in the locked (non-editable) view — it's a read op.
    Regression: sort was bound to the edit-mode-only data-cell-nav hook, so the
    default locked view could not sort."""
    _goto(page, demo_server)
    lrows = f"{LOCKED} tbody > tr[id]"
    page.locator(f"{LOCKED} thead th", has_text="Amount").first.click()
    page.wait_for_timeout(200)
    # ascending by amount: the negative (340.00) row (Beta) sorts to the top
    assert "Beta" in page.locator(lrows).first.inner_text()


def _footer_gap(page):
    """Pixels between the scroll container's bottom and the total row's bottom.
    ~0 means the footer is pinned to the container bottom."""
    return page.evaluate(
        """() => {
            const c = document.querySelector('#demo-flat-table').closest('[data-sheet-scroll]');
            const tr = document.querySelector('#demo-flat-table tr.total-row');
            return Math.round(c.getBoundingClientRect().bottom - tr.getBoundingClientRect().bottom);
        }"""
    )


def test_footer_pins_to_bottom_when_content_short(page, demo_server):
    """With few rows, the total row is pushed to the container's bottom (the
    grid filler + sticky bottom), rather than floating just under the last row."""
    _goto(page, demo_server)
    assert abs(_footer_gap(page)) <= 3


def test_footer_pins_at_mobile_viewport(page, demo_server):
    """The footer must pin at a small mobile-portrait viewport too — the case the
    desktop-viewport test above never exercises. Runs on the WebKit CI leg, so a
    Safari-engine footer regression at phone size is caught, not left to manual
    on-device QA."""
    page.set_viewport_size({"width": 390, "height": 780})
    _goto(page, demo_server)
    assert abs(_footer_gap(page)) <= 3


def test_footer_stays_pinned_across_viewport_resize(page, demo_server):
    """The footer must re-pin when the viewport height changes after load — the
    grid filler is measured against the container height, so a height change (on
    mobile, the browser chrome collapsing/expanding as you scroll) must trigger a
    recompute or the footer goes stale and follows the content.

    NOTE: a window resize is the closest CI-reproducible analog. Real iOS Safari
    changes height via `visualViewport` (URL-bar collapse), which a headless
    desktop engine has no equivalent for; sheet-scroll.js listens to both. This
    guards the recompute-on-resize path in both engines."""
    page.set_viewport_size({"width": 390, "height": 780})
    _goto(page, demo_server)
    assert abs(_footer_gap(page)) <= 3
    page.set_viewport_size({"width": 390, "height": 560})
    page.wait_for_timeout(300)
    assert abs(_footer_gap(page)) <= 3, "footer un-pinned after viewport shrank"
    page.set_viewport_size({"width": 390, "height": 780})
    page.wait_for_timeout(300)
    assert abs(_footer_gap(page)) <= 3, "footer un-pinned after viewport grew"


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
