"""Overview page e2e tests (issue #105)."""

import pytest

pytestmark = pytest.mark.e2e

_CARDS = ("net_worth_liquid", "projection_warnings")


def _tap(page, selector):
    """Click where an element is drawn, like a finger would. The card's
    stretched link covers its contents, so Playwright's element click
    (which refuses when another element is on top) can't be used."""
    box = page.locator(selector).bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


def test_snapshot_root_lands_on_overview(page, flask_server):
    page.goto(f"{flask_server}/s/test_finances")
    assert "Overview" in page.title()
    for key in _CARDS:
        assert page.locator(f"[data-overview-card='{key}']").is_visible()
    liquid = page.locator("[data-figure='liquid']")
    assert liquid.locator("[data-headline]").inner_text().strip() == "$5,000"
    assert liquid.locator("[data-change]").is_visible()
    assert "Projected change by " in page.locator("[data-horizon]").inner_text()


def test_overview_is_first_in_the_nav(page, flask_server):
    page.goto(f"{flask_server}/s/test_finances/holdings")
    first = page.locator("[data-nav-sidebar] a").first
    assert first.inner_text().strip() == "Overview"
    first.click()
    page.wait_for_url("**/s/test_finances")
    active = page.locator("[data-nav-sidebar] a[aria-current='page']")
    assert active.inner_text().strip() == "Overview"


def test_net_worth_card_links_to_projections(page, flask_server):
    """The whole Net worth / Liquid card is the link."""
    page.goto(f"{flask_server}/s/test_finances")
    _tap(page, "[data-overview-card='net_worth_liquid'] [data-figure='liquid']")
    page.wait_for_url("**/projections?months=12")
    assert page.locator("[data-projection-table]").is_visible()


def test_warnings_open_projections_on_that_month(page, flask_server, _db_path):
    """An account below its minimum gets one line that opens its first month."""
    from sqlalchemy import create_engine, update

    from fintrack.core.models import cash_details

    engine = create_engine(f"sqlite:///{_db_path}")
    with engine.begin() as conn:
        conn.execute(update(cash_details).values(minimum_balance=6000))
    engine.dispose()

    page.goto(f"{flask_server}/s/test_finances")
    card = page.locator("[data-overview-card='projection_warnings']")
    warnings = card.locator("[data-warnings]")
    assert (
        "1 account dips below its minimum" in card.locator("[data-status]").inner_text()
    )
    assert "under $6,000 min from" in warnings.inner_text()
    warnings.locator("a").first.click()
    page.wait_for_url("**/projections?months=12&month=*")
    assert page.locator("[data-month-breakdown]").is_visible()


def test_low_point_opens_its_month(page, flask_server):
    page.goto(f"{flask_server}/s/test_finances")
    card = page.locator("[data-overview-card='projection_warnings']")
    assert card.locator("[data-status]").inner_text().strip() == "All clear"
    low = card.locator("[data-lowest]")
    month = low.inner_text().split("·")[-1].strip()
    low.click()
    page.wait_for_url("**/projections?months=12&month=*")
    assert month in page.locator("[data-month-breakdown]").inner_text()


def test_warnings_card_body_opens_projections(page, flask_server):
    """Clicking the card outside its line links opens Projections (no month)."""
    page.goto(f"{flask_server}/s/test_finances")
    _tap(page, "[data-overview-card='projection_warnings'] [data-status]")
    page.wait_for_url("**/projections?months=12")
    assert page.locator("[data-month-breakdown]").count() == 0


def test_overview_on_a_phone(page, flask_server):
    """One column of cards, no sideways scroll."""
    page.set_viewport_size({"width": 375, "height": 740})
    page.goto(f"{flask_server}/s/test_finances")
    boxes = [
        page.locator(f"[data-overview-card='{key}']").bounding_box() for key in _CARDS
    ]
    assert len({round(b["x"]) for b in boxes}) == 1  # stacked
    assert all(b["x"] >= 0 and b["x"] + b["width"] <= 375 for b in boxes)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_overview_on_wide_screens_uses_columns(page, flask_server):
    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto(f"{flask_server}/s/test_finances")
    boxes = [
        page.locator(f"[data-overview-card='{key}']").bounding_box() for key in _CARDS
    ]
    assert len({round(b["x"]) for b in boxes}) == 2  # side by side
    assert len({round(b["y"]) for b in boxes}) == 1


def test_overview_of_an_empty_snapshot(page, flask_server):
    page.goto(f"{flask_server}/")
    page.request.post(f"{flask_server}/files/new", form={"name": "blank"})
    try:
        page.goto(f"{flask_server}/s/blank")
        assert page.locator("[data-overview-empty]").is_visible()
        for key in _CARDS:
            assert page.locator(f"[data-overview-card='{key}']").is_visible()
        page.locator("[data-overview-empty] a", has_text="Holdings").click()
        page.wait_for_url("**/s/blank/holdings")
    finally:
        page.request.post(f"{flask_server}/files/delete", form={"name": "blank"})
