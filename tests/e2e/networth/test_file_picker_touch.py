"""File picker on a touch screen: a closed picker must take no taps.

On touch screens the file-row actions (Delete) are always shown inside the
picker. While the picker was closed they were still tappable, invisible, over
the page: tapping the Transactions sheet asked to delete a household.
"""

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "viewport": {"width": 390, "height": 844},
        "has_touch": True,
        "is_mobile": True,
    }


def _hits_inside_picker(page) -> list[bool]:
    """For each button/link in the picker: does a tap at its center land
    inside the picker?"""
    return page.evaluate(
        """() => {
            const dd = document.querySelector('[data-file-picker-dropdown]');
            return [...dd.querySelectorAll('button, a')].map(el => {
                const r = el.getBoundingClientRect();
                const hit = document.elementFromPoint(
                    r.left + r.width / 2, r.top + r.height / 2);
                return !!hit && dd.contains(hit);
            });
        }"""
    )


def test_closed_picker_takes_no_taps(page, flask_server):
    # Delete buttons show on the rows of the *other* snapshots: add one.
    page.goto(f"{flask_server}/")
    page.request.post(f"{flask_server}/files/new", form={"name": "touch-other"})
    try:
        page.goto(f"{flask_server}/s/test_finances/holdings")
        delete = page.locator("[data-file-picker-dropdown] .file-row-actions button")
        assert delete.count() >= 1
        hits = _hits_inside_picker(page)
        assert hits, "picker has no buttons to check"
        assert not any(hits)

        page.locator("[data-file-picker]").tap()
        page.locator("[data-file-picker-dropdown]").wait_for(state="visible")
        page.wait_for_timeout(200)  # opacity transition
        assert any(_hits_inside_picker(page))
    finally:
        page.request.post(f"{flask_server}/files/delete", form={"name": "touch-other"})
