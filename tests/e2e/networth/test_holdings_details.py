"""Holdings' Details column (issue #142): every band shares one column spine,
each band's own fields sit in one Details cell on a CSS grid, and those fields
stay click-to-edit, keyboard-navigable, and sortable by their labels."""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

CARD_ROW = "#holdings-credit tr[data-reorder-index]:has-text('Rewards Card')"


def _x(page, selector: str) -> float:
    return page.locator(selector).first.bounding_box()["x"]


def test_due_linked_as_of_line_up_across_bands(page, flask_server):
    page.set_viewport_size({"width": 1600, "height": 900})
    page.goto(f"{flask_server}/s/test_finances/holdings")
    for col in (5, 6, 7):  # Due, Linked, As Of
        xs = {
            round(
                _x(
                    page,
                    f"#holdings-{band} .sheet-group-header > th:nth-child({col + 1})",
                )
            )
            for band in ("cash", "credit", "loan", "asset")
        }
        assert len(xs) == 1, (col, xs)


def test_details_label_lines_up_with_its_field(page, flask_server):
    page.goto(f"{flask_server}/s/test_finances/holdings")
    label = page.locator('#holdings-credit [data-sort-key="f-statement"]')
    field = page.locator(f'{CARD_ROW} [data-sort-field="statement"]')
    lb, fb = label.bounding_box(), field.bounding_box()
    assert lb["x"] == pytest.approx(fb["x"], abs=0.5)
    assert lb["width"] == pytest.approx(fb["width"], abs=0.5)


def test_details_field_edits_in_place_and_tabs_on(page, flask_server):
    """Clicking a Details field opens just that field's editor; Tab saves and
    opens the next editable field (Available and Used are computed, so it
    skips to Rewards)."""
    page.goto(f"{flask_server}/s/test_finances/holdings?edit=1")
    page.locator(f'{CARD_ROW} [data-cell-key="f-limit"]').click()
    editor = page.locator(
        f'{CARD_ROW} .cell-editing[data-cell-key="f-limit"] input[name="value"]'
    )
    editor.wait_for()
    editor.fill("12000")
    editor.press("Tab")
    rewards = page.locator(
        f'{CARD_ROW} .cell-editing[data-cell-key="f-rewards"] input[name="value"]'
    )
    rewards.wait_for()
    assert rewards.evaluate("e => e === document.activeElement")
    rewards.press("Escape")
    limit = page.locator(f'{CARD_ROW} [data-sort-field="limit"]')
    expect(limit).to_contain_text("12,000.00")
    # Restore the fixture value for the other tests sharing this server.
    page.locator(f'{CARD_ROW} [data-cell-key="f-limit"]').click()
    editor.fill("10000")
    editor.press("Enter")
    expect(limit).to_contain_text("10,000.00")


def test_details_label_sorts_its_band(page, flask_server):
    """A Details label sorts its band by that field (asc -> desc), keeps the
    choice in the URL, and leaves the other bands alone."""
    page.goto(f"{flask_server}/s/test_finances/holdings")
    rows = page.locator("#holdings-asset tr[data-reorder-index]")
    assert rows.first.inner_text().find("Primary Residence") >= 0
    qty = page.locator('#holdings-asset [data-sort-key="f-qty"]')
    qty.click()
    qty.click()
    assert "Savings Bonds" in rows.first.inner_text()
    assert "↓" in qty.inner_text()
    assert "sort_asset_col=f-qty" in page.url and "sort_asset_dir=desc" in page.url
    page.reload()
    assert "Savings Bonds" in rows.first.inner_text()
