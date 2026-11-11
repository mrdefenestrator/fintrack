"""iPadOS 26 window buttons must not cover the menu button (issue #126).

In a resized window, iPadOS draws close/minimize/full-screen buttons over the
web app's top-left corner. No env() inset reports them, so base.html flags an
installed iPad app whose window is narrower than the screen and the header
makes room. These tests fake that environment with an init script.
"""

import pytest

pytestmark = pytest.mark.e2e

# iPadOS Safari reports a Mac user agent; touch points tell it apart.
_IPAD_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/26.0 Safari/605.1.15"
)

# iOS keeps screen.width/height in portrait whatever the orientation.
_SCREEN = {"width": 1024, "height": 1366}


def _fake(context, *, standalone: bool, touch_points: int = 5):
    context.add_init_script(
        f"""
        Object.defineProperty(navigator, 'standalone', {{ get: () => {str(standalone).lower()} }});
        Object.defineProperty(navigator, 'maxTouchPoints', {{ get: () => {touch_points} }});
        Object.defineProperty(screen, 'width', {{ get: () => {_SCREEN["width"]} }});
        Object.defineProperty(screen, 'height', {{ get: () => {_SCREEN["height"]} }});
        """
    )


def _open(browser, flask_server, width, **fake):
    context = browser.new_context(
        user_agent=_IPAD_UA, viewport={"width": width, "height": 700}
    )
    _fake(context, **fake)
    page = context.new_page()
    page.goto(f"{flask_server}/s/test_finances/holdings")
    return context, page


def _flagged(page) -> bool:
    return page.evaluate(
        "document.documentElement.hasAttribute('data-window-controls')"
    )


def _menu_left(page) -> float:
    return page.locator("[data-nav-toggle]").bounding_box()["x"]


def test_windowed_ipad_app_clears_window_buttons(browser, flask_server):
    context, page = _open(browser, flask_server, 800, standalone=True)
    try:
        assert _flagged(page)
        assert _menu_left(page) >= 80
    finally:
        context.close()


@pytest.mark.parametrize("width", [_SCREEN["width"], _SCREEN["height"]])
def test_full_screen_ipad_app_keeps_menu_at_edge(browser, flask_server, width):
    """Portrait and landscape full screen: no buttons, no extra room."""
    context, page = _open(browser, flask_server, width, standalone=True)
    try:
        assert not _flagged(page)
        assert _menu_left(page) < 40
    finally:
        context.close()


def test_safari_tab_not_flagged(browser, flask_server):
    context, page = _open(browser, flask_server, 800, standalone=False)
    try:
        assert not _flagged(page)
    finally:
        context.close()


def test_mac_not_flagged(browser, flask_server):
    """A real Mac has no touch points, even if it reports standalone."""
    context, page = _open(browser, flask_server, 800, standalone=True, touch_points=0)
    try:
        assert not _flagged(page)
    finally:
        context.close()


def test_flag_follows_window_resize(browser, flask_server):
    context, page = _open(browser, flask_server, 800, standalone=True)
    try:
        assert _flagged(page)
        page.set_viewport_size({"width": _SCREEN["height"], "height": 700})
        page.wait_for_function(
            "!document.documentElement.hasAttribute('data-window-controls')"
        )
        assert _menu_left(page) < 40
    finally:
        context.close()
