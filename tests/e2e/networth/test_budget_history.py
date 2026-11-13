"""E2E: Budget history (issue #136) — the month pager, an edit made in a past
month applying until the next change, ending an entry, and the history row."""

from datetime import date, datetime

import pytest

from tests.e2e.networth.conftest import enable_edit_mode

pytestmark = pytest.mark.e2e


def _this_month() -> date:
    return datetime.now().astimezone().date().replace(day=1)


def _shift(d: date, months: int) -> date:
    i = d.year * 12 + d.month - 1 + months
    return date(i // 12, i % 12 + 1, 1)


def _rent_row(page):
    return page.locator("#budget-table tr[data-reorder-index]").filter(has_text="Rent")


def _edit_rent_amount(page, value: str) -> None:
    _rent_row(page).locator("td").nth(2).click()
    input_el = page.locator("#budget-table input[name='value']")
    input_el.wait_for(state="visible")
    input_el.click()
    input_el.fill(value)
    with page.expect_response(
        lambda r: "/budget/update/" in r.url and r.request.method == "POST"
    ):
        page.locator("#budget-table tr.total-row td").first.click()
    page.wait_for_load_state("networkidle")


def test_month_pager_pages_through_the_plan(page, flask_server):
    now = _this_month()
    page.goto(f"{flask_server}/s/test_finances/budget")
    label = page.locator("[data-pager-label]")
    assert label.inner_text() == f"{now:%b %Y}"

    page.locator("[data-pager] a[title='Earlier']").click()
    page.wait_for_load_state("networkidle")
    prev = _shift(now, -1)
    assert label.inner_text() == f"{prev:%b %Y}"
    assert f"ym={prev:%Y-%m}" in page.url
    assert page.get_by_text(f"The plan as it was in {prev:%b %Y}").is_visible()

    page.locator("[data-pager-latest]").click()
    page.wait_for_load_state("networkidle")
    assert label.inner_text() == f"{now:%b %Y}"


def test_past_edit_applies_until_the_next_change(page, flask_server):
    prev = _shift(_this_month(), -1)
    base = f"{flask_server}/s/test_finances/budget"

    page.goto(base)
    enable_edit_mode(page)
    _edit_rent_amount(page, "2100")

    page.goto(f"{base}?ym={prev:%Y-%m}&edit=1")
    assert page.get_by_text(f"Edits apply from {prev:%b %Y}").is_visible()
    _edit_rent_amount(page, "1950")

    page.goto(f"{base}?ym={prev:%Y-%m}")
    assert "($1,950.00)" in _rent_row(page).inner_text()
    assert _rent_row(page).locator("[data-budget-mark='changed']").count() == 1
    page.goto(f"{base}?ym={_shift(prev, -1):%Y-%m}")
    assert "($1,950.00)" not in _rent_row(page).inner_text()
    page.goto(base)
    assert "($2,100.00)" in _rent_row(page).inner_text()


def test_end_and_history(page, flask_server):
    now = _this_month()
    base = f"{flask_server}/s/test_finances/budget"
    page.goto(base)
    enable_edit_mode(page)

    with page.expect_response(lambda r: "/budget/end/" in r.url):
        _rent_row(page).locator("[data-budget-end]").click()
    page.wait_for_load_state("networkidle")
    assert _rent_row(page).locator("[data-budget-resume]").is_visible()

    with page.expect_response(lambda r: "/budget/history/" in r.url):
        _rent_row(page).locator("[data-budget-history]").click()
    history = page.locator("tr[data-history-for]")
    history.wait_for(state="visible")
    assert history.locator("[data-version='base']").is_visible()
    assert history.locator("input[name='end']").input_value() == f"{now:%Y-%m}"

    page.goto(f"{base}?ym={_shift(now, 1):%Y-%m}")
    assert _rent_row(page).count() == 0
