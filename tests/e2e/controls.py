"""Shared e2e check for the page-top control layout every pager page uses.

The time pager sits alone on its row (`nav.pager_row`) above the sheet; every
other control leads the sheet's filter bar (`sheet_filter_bar.filter_bar`),
which on a phone (portrait, or landscape) is a single row: lead toggles, the
"Filters" trigger, Reset.
"""

# Phone viewports (width, height): portrait at common widths, plus a landscape
# phone, which counts as a phone too (short, though wider than sm — the
# `phone:` / `wide:` screens in tailwind.config.js, #146).
PHONE_VIEWPORTS = [(360, 800), (390, 844), (430, 932), (844, 390)]
PHONE_IDS = ["360", "390", "430", "landscape"]


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


def open_row_menu(row):
    """Open a sheet row's ⋯ actions menu (partials/row_actions.html, issue
    #148) and return it: the row's popover, now showing. Pick an item from it
    by its data attribute (`[data-budget-end]`, `[data-row-delete]`, …) or by
    role and name."""
    row.locator(".row-menu-btn").click()
    menu = row.locator(".row-menu")
    menu.wait_for(state="visible")
    return menu
