"""Shared e2e check for the page-top control layout every pager page uses.

The time pager sits alone on its row (`nav.pager_row`) above the sheet; every
other control leads the sheet's filter bar (`sheet_filter_bar.filter_bar`),
which on a phone is a single row: lead toggles, the "Filters" trigger, Reset.
"""

PHONE_WIDTHS = [360, 390, 430]


def assert_controls_layout(page, width: int) -> None:
    """The pager row is above the filter bar, and the bar's visible controls
    share one row inside the viewport's 16px gutter."""
    pager_row = page.locator("[data-pager-row]").bounding_box()
    bar = page.locator("[data-filter-bar]").bounding_box()
    assert pager_row["y"] + pager_row["height"] <= bar["y"]

    items = page.locator("[data-filter-bar] > div > :not([data-filter-panel])")
    boxes = [b for i in range(items.count()) if (b := items.nth(i).bounding_box())]
    assert boxes
    assert len({round(b["y"] + b["height"] / 2) for b in boxes}) == 1
    assert max(b["x"] + b["width"] for b in boxes) <= width - 16
    assert bar["x"] + bar["width"] <= width
