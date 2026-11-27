"""Phone layout (issue #146): a phone is a narrow screen or a short one (a
landscape phone), and both get the same denser sheets — 24px rows, 13px text,
slimmer padding — and (landscape) a slimmer header. Wider screens keep 28px
rows."""

import pytest

pytestmark = pytest.mark.e2e

PORTRAIT = (390, 844)
LANDSCAPE = (844, 390)
DESKTOP = (1280, 800)


def _open(page, flask_server, viewport, path="holdings", inset_bottom=None):
    """Open a sheet at `viewport`. With `inset_bottom`, emulate an iPhone's
    home-indicator safe area first; returns whether the browser could (the
    override is a recent DevTools command)."""
    width, height = viewport
    page.set_viewport_size({"width": width, "height": height})
    emulated = False
    if inset_bottom is not None:
        cdp = page.context.new_cdp_session(page)
        try:
            cdp.send(
                "Emulation.setSafeAreaInsetsOverride",
                {"insets": {"top": 0, "bottom": inset_bottom, "left": 0, "right": 0}},
            )
            emulated = True
        except Exception:  # noqa: BLE001 — older Chromium: no inset emulation
            emulated = False
    page.goto(f"{flask_server}/s/test_finances/{path}")
    return emulated


def _height(locator) -> float:
    return locator.bounding_box()["height"]


@pytest.mark.parametrize(
    "viewport, row_h, heading_h",
    [(PORTRAIT, 24, 24), (LANDSCAPE, 24, 24), (DESKTOP, 28, 32)],
    ids=["portrait", "landscape", "desktop"],
)
def test_sheet_rows_are_denser_on_phones(
    page, flask_server, viewport, row_h, heading_h
):
    """On a phone (either orientation) data rows, band headings and column
    headers are all 24px; a wider screen keeps 28px rows and 32px headings."""
    _open(page, flask_server, viewport)
    tbl = page.locator("table.grouped-sheet")
    assert _height(tbl.locator("tr.sheet-group-row").first) == pytest.approx(row_h)
    assert _height(tbl.locator("tr.sheet-group-heading").first) == pytest.approx(
        heading_h
    )
    assert _height(tbl.locator("tr.sheet-group-header").first) == pytest.approx(row_h)


@pytest.mark.parametrize(
    "viewport", [PORTRAIT, LANDSCAPE], ids=["portrait", "landscape"]
)
def test_phone_sheet_keeps_its_gutter_and_one_row_footer(page, flask_server, viewport):
    """A phone's sheet ends above main's usual bottom gutter (8px, or the
    bottom safe-area inset if larger) with its bottom border, and its pinned
    footer rows stay one row tall whatever the inset."""
    height = viewport[1]
    emulated = _open(page, flask_server, viewport, inset_bottom=34)
    frame = page.locator("[data-sheet-scroll]")
    fb = frame.bounding_box()
    gap = 34 if emulated else 8
    assert fb["y"] + fb["height"] == pytest.approx(height - gap, abs=1)
    assert frame.evaluate("e => getComputedStyle(e).borderBottomWidth") == "1px"
    for row in page.locator("tr.total-row").all():
        assert row.bounding_box()["height"] == pytest.approx(24)


def test_sheet_keeps_its_gutter_on_desktop(page, flask_server):
    """Wider screens are unchanged: the sheet ends above an 8px gutter."""
    height = DESKTOP[1]
    _open(page, flask_server, DESKTOP)
    frame = page.locator("[data-sheet-scroll]").bounding_box()
    assert frame["y"] + frame["height"] == pytest.approx(height - 8, abs=1)


@pytest.mark.parametrize(
    "viewport, header_h",
    [(PORTRAIT, 48), (LANDSCAPE, 40), (DESKTOP, 48)],
    ids=["portrait", "landscape", "desktop"],
)
def test_header_is_slimmer_in_landscape(page, flask_server, viewport, header_h):
    """A landscape phone has no height to spare: the app header is 40px there
    (48px elsewhere), and the narrow-screen nav drawer opens right under it."""
    _open(page, flask_server, viewport)
    assert _height(page.locator("#main-header")) == pytest.approx(header_h)
    if viewport != DESKTOP:
        page.locator("[data-nav-toggle]").click()
        nav = page.locator("[data-nav-sidebar]")
        nav.wait_for(state="visible")
        assert nav.bounding_box()["y"] == pytest.approx(header_h)


def test_projections_summary_fits_beside_the_pager_in_landscape(page, flask_server):
    """On a landscape phone the end-of-window summary shares the pager's row:
    whole dollars and no "By <month>:" lead (the pager names the window)."""
    _open(page, flask_server, LANDSCAPE, path="projections")
    pager = page.locator("[data-proj-window]").bounding_box()
    line = page.locator("[data-summary='line']")
    box = line.bounding_box()
    assert box["y"] < pager["y"] + pager["height"]
    assert box["x"] > pager["x"] + pager["width"]
    text = line.inner_text()
    assert not text.startswith("By ")
    assert "." not in page.locator("[data-summary='liquid']").inner_text()


def test_projections_summary_keeps_cents_on_desktop(page, flask_server):
    _open(page, flask_server, DESKTOP, path="projections")
    assert page.locator("[data-summary='line']").inner_text().startswith("By ")
    assert "." in page.locator("[data-summary='liquid']").inner_text()


def test_scroller_is_isolated_on_touch_screens(browser, flask_server):
    """iOS draws a scroller's indicator inside the scrolling view, and the
    sticky header rows (z-indexed) covered it unless the scroller is its own
    stacking context. Touch screens isolate it; a desktop doesn't (its top
    scroll shadow tucks under the sticky chrome)."""
    for touch, expected in ((True, "isolate"), (False, "auto")):
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844}, has_touch=touch, is_mobile=touch
        )
        page = ctx.new_page()
        page.goto(f"{flask_server}/s/test_finances/holdings")
        scroller = page.locator(".table-scroll-container")
        assert scroller.evaluate("e => getComputedStyle(e).isolation") == expected
        ctx.close()


@pytest.mark.parametrize(
    "viewport, faded", [(PORTRAIT, True), (DESKTOP, False)], ids=["phone", "desktop"]
)
def test_overview_fades_out_at_the_bottom_on_phones(
    page, flask_server, viewport, faded
):
    """On a phone, Overview's cards fade out over the last 1.5rem of their
    scrolling box instead of being sliced off at its edge, and the content's
    extra bottom padding lets the last card scroll clear of the fade."""
    _open(page, flask_server, viewport, path="")
    box = page.locator("[data-overview]")
    mask = box.evaluate(
        "e => getComputedStyle(e).maskImage || getComputedStyle(e).webkitMaskImage"
    )
    assert ("gradient" in mask) == faded
    if faded:
        box.evaluate("e => { e.scrollTop = e.scrollHeight; }")
        last = page.locator("[data-overview-card]").last.bounding_box()
        b = box.bounding_box()
        assert last["y"] + last["height"] <= b["y"] + b["height"] - 24
