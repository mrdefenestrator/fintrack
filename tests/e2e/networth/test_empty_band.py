"""Empty-band message stays in view on a narrow screen (issue #104)."""

import pytest

pytestmark = pytest.mark.e2e


def test_empty_band_message_visible_on_phone_width(page, flask_server):
    """The empty-state cell spans every column, so a centered message landed far
    off-screen on a phone. It must sit inside the frame, also when scrolled."""
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{flask_server}/s/test_finances/holdings?type=checking")
    msg = page.locator(".sheet-empty-msg", has_text="No loans match")
    frame = page.locator(".table-scroll-container")

    def inside_frame():
        m = msg.bounding_box()
        f = frame.bounding_box()
        return m["x"] >= f["x"] and m["x"] + m["width"] <= f["x"] + f["width"]

    assert inside_frame()
    frame.evaluate("e => e.scrollLeft = e.scrollWidth")
    page.wait_for_timeout(100)
    assert inside_frame()
