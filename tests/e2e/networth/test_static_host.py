"""Static files from a separate host (FINTRACK_STATIC_URL, issue #69).

The PR preview's app Lambda answers one request at a time and returns 429 for
the rest, which dropped scripts and stylesheets at random (#92: the sheet
footer stopped pinning). The preview now serves /static from a second,
stateless function. These tests reproduce the app host's 429s and check that,
with the prefix set, every sheet still gets its CSS and JS.
"""

import os
import subprocess
import sys

import pytest

from .conftest import PROJECT_ROOT, _free_port, _wait_for_server

pytestmark = pytest.mark.e2e

SHEETS = ["holdings", "budget", "projections", "transactions", "merchants"]


@pytest.fixture(scope="module")
def prefixed_server(flask_server, _db_path):
    """A second app server on the shared test DB whose static files come
    from ``flask_server`` (standing in for the preview's static function)."""
    port = _free_port()
    env = {
        **os.environ,
        "FINTRACK_DB": str(_db_path),
        "FINTRACK_PORT": str(port),
        "FINTRACK_STATIC_URL": flask_server,
        "FLASK_DEBUG": "0",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "web.app"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_server(port)
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _throttle_static(page, app_url: str) -> list[str]:
    """Answer every /static request to the app host with 429, as the
    one-at-a-time preview Lambda does for parallel requests."""
    throttled: list[str] = []

    def _429(route):
        throttled.append(route.request.url)
        route.fulfill(status=429, body="Too Many Requests")

    page.route(f"{app_url}/static/**", _429)
    return throttled


def _footer_gap(page) -> float:
    """Pixels between the sheet's last total row and its container's bottom."""
    return page.evaluate(
        """() => {
            const c = document.querySelector('[data-sheet-scroll]');
            const rows = c.querySelectorAll('tr.total-row');
            const last = rows[rows.length - 1];
            return c.getBoundingClientRect().bottom
                - last.getBoundingClientRect().bottom;
        }"""
    )


def _assets_loaded(page) -> dict:
    return page.evaluate(
        """() => ({
            alpine: !!window.Alpine,
            htmx: !!window.htmx,
            sortable: !!window.Sortable,
            // A Tailwind utility on <body>; unstyled pages keep it transparent.
            tailwind: getComputedStyle(document.body).backgroundColor
                !== 'rgba(0, 0, 0, 0)',
        })"""
    )


@pytest.mark.parametrize("sheet", SHEETS)
def test_prefixed_static_survives_app_host_429s(page, prefixed_server, sheet):
    page.set_viewport_size({"width": 1400, "height": 1000})
    throttled = _throttle_static(page, prefixed_server)
    page.goto(f"{prefixed_server}/s/test_finances/{sheet}")
    page.wait_for_load_state("load")

    # Only the manifest stays on the app's own origin (its start_url/scope
    # resolve against it); everything the page needs comes from the prefix.
    assert all(u.endswith("/manifest.webmanifest") for u in throttled), throttled
    assert _assets_loaded(page) == {
        "alpine": True,
        "htmx": True,
        "sortable": True,
        "tailwind": True,
    }
    if page.locator("[data-sheet-scroll] tr.total-row").count():
        assert abs(_footer_gap(page)) <= 2


def test_without_prefix_429s_break_the_page(page, flask_server):
    """Control: the same 429s on an unprefixed app reproduce #92, so the test
    above would catch a prefix that stopped applying."""
    page.set_viewport_size({"width": 1400, "height": 1000})
    throttled = _throttle_static(page, flask_server)
    page.goto(f"{flask_server}/s/test_finances/holdings")
    page.wait_for_load_state("load")
    assert any("/static/dist/tailwind.css" in u for u in throttled)
    assert _assets_loaded(page) == {
        "alpine": False,
        "htmx": False,
        "sortable": False,
        "tailwind": False,
    }
