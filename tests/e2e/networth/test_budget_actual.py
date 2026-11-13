"""The Budget sheet's actuals (issues #53, #73, #143) — each entry's Actual /
Delta / Status for the month, the Status filter and grouping, link
suggestions in edit mode, the Actual cell's link to its transactions, the
Recurring view, and the redirect from the old Budget vs Actual page.

Pinned to April 2026, when the fixture's monthly entries (Salary, Freelance,
Rent, Groceries) are all active and nothing is linked.
"""

import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, insert, select

from fintrack.core.models import holdings, imports, snapshots, transactions
from tests.e2e.controls import PHONE_WIDTHS, assert_controls_layout
from tests.e2e.networth.conftest import enable_edit_mode

pytestmark = pytest.mark.e2e

_URL = "/s/test_finances/budget"
_APRIL = f"{_URL}?ym=2026-04"


def _entry_row(page, description):
    """A data row (not the band heading, whose label may match too)."""
    return page.locator("#budget-table tr[data-reorder-index]").filter(
        has=page.get_by_text(description, exact=True)
    )


def test_sheet_shows_actual_delta_status(page, flask_server):
    page.goto(f"{flask_server}{_APRIL}")
    header = page.locator("#budget-table .sheet-group-header").first.inner_text()
    for column in ("This month", "Actual", "Delta", "Status"):
        assert column in header
    assert "Remaining" not in header
    rent = _entry_row(page, "Rent")
    assert (
        rent.locator("[data-status='unlinked']").get_by_text("Not linked").is_visible()
    )


def test_future_month_has_no_actuals(page, flask_server):
    today = datetime.now().astimezone().date()
    later = date(today.year + 1, today.month, 1)
    page.goto(f"{flask_server}{_URL}?ym={later:%Y-%m}")
    rent = _entry_row(page, "Rent")
    assert rent.locator("td[title='No actuals for a future month']").count() == 3


def test_old_url_redirects_to_the_month(page, flask_server):
    page.goto(f"{flask_server}{_URL}/actual?year=2026&month=4&kind=expense")
    page.wait_for_url("**/budget?kind=expense&ym=2026-04")
    assert page.locator("text=Apr 2026").first.is_visible()
    assert _entry_row(page, "Salary").count() == 0


def test_status_filter(page, flask_server):
    page.goto(f"{flask_server}{_APRIL}")
    page.locator(
        "[data-filter-panel] button.filter-dropdown-trigger:has-text('Status')"
    ).click()
    page.locator("[data-filter-panel] input[name='status'][value='matched']").click()
    page.wait_for_url("**status=matched**")
    assert "ym=2026-04" in page.url
    assert page.locator("#budget-table tr[data-reorder-index]").count() == 0


def test_group_by_status(page, flask_server):
    page.goto(f"{flask_server}{_APRIL}&group=status")
    headings = page.locator("#budget-table .sheet-group-heading").all_inner_texts()
    assert any("NOT LINKED" in h.upper() for h in headings)
    assert _entry_row(page, "Rent").is_visible()


# --- Link suggestions (issues #70, #143) -------------------------------------


def _add_rent_charge(db_path) -> None:
    """An unlinked April rent charge on Main Checking, matching the fixture's
    $1500 Rent entry exactly, so it's suggested with high confidence."""
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.connect() as conn:
        acct = conn.execute(
            select(holdings.c.id)
            .join(snapshots, holdings.c.snapshot_id == snapshots.c.id)
            .where(
                snapshots.c.name == "test_finances",
                holdings.c.name == "Main Checking",
            )
        ).scalar_one()
        imp = conn.execute(
            insert(imports).values(
                account_id=acct,
                holding_group="cash",
                filename="e2e.ofx",
                file_hash=uuid.uuid4().hex,
                status="confirmed",
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(transactions).values(
                import_id=imp,
                account_id=acct,
                date=date(2026, 4, 1),
                amount="-1500.00",
                raw_description="LANDLORD LLC",
                normalized_merchant="Landlord",
                fingerprint=uuid.uuid4().hex,
            )
        )
        conn.commit()
    engine.dispose()


def test_suggestion_count_links_to_edit_mode(page, flask_server, _db_path):
    _add_rent_charge(_db_path)
    page.goto(f"{flask_server}{_APRIL}")
    assert page.locator("[data-suggestion-apply]").count() == 0
    assert _entry_row(page, "Rent").get_by_text("1 suggested").is_visible()
    page.click("a[data-actuals-suggested]")
    page.wait_for_url("**edit=1**")
    assert "ym=2026-04" in page.url
    assert page.locator("[data-suggestion]").get_by_text("Landlord").is_visible()


def test_apply_suggestion_links_transaction(page, flask_server, _db_path):
    _add_rent_charge(_db_path)
    page.goto(f"{flask_server}{_APRIL}")
    enable_edit_mode(page)
    assert page.locator("[data-actuals-suggested]").is_visible()
    page.click("[data-suggestion-apply]")
    page.locator("[data-suggestion]").wait_for(state="detached")
    assert _entry_row(page, "Rent").get_by_text("Matched").is_visible()
    # The pager row's findings were swapped out-of-band with the sheet.
    assert page.locator("#budget-findings").count() == 1
    assert page.locator("[data-actuals-suggested]").count() == 0
    assert "ym=2026-04" in page.url


def test_actual_opens_its_transactions(page, flask_server, _db_path):
    _add_rent_charge(_db_path)
    page.goto(f"{flask_server}{_APRIL}&edit=1")
    page.click("[data-suggestion-apply]")
    page.locator("[data-suggestion]").wait_for(state="detached")
    page.goto(f"{flask_server}{_APRIL}")
    _entry_row(page, "Rent").locator("a[data-actual-link]").click()
    page.wait_for_url("**/transactions?**budget=**")
    assert "year=2026" in page.url and "month=4" in page.url
    rows = page.locator("#txn-sheet tr[id^='txn-row-']")
    assert rows.count() == 1
    assert rows.first.get_by_text("Landlord", exact=True).is_visible()


def test_dismissed_suggestion_stays_dismissed(page, flask_server, _db_path):
    _add_rent_charge(_db_path)
    page.goto(f"{flask_server}{_APRIL}&edit=1")
    page.click("[data-suggestion-dismiss]")
    page.locator("[data-suggestion]").wait_for(state="detached")
    page.reload()
    assert page.locator("[data-actuals-suggested]").count() == 0
    assert _entry_row(page, "Rent").get_by_text("Not linked").is_visible()


def test_suggestion_row_stays_under_its_entry_when_sorted(page, flask_server, _db_path):
    """The sheet sorts with the shared group-sort.js; a suggestion detail row
    belongs to the entry above it and moves with it."""
    _add_rent_charge(_db_path)
    page.goto(f"{flask_server}{_APRIL}&edit=1&group=none")
    header = page.locator("#budget-table th[data-sort-key='c1']")  # Description
    for _ in range(2):  # ascending, then descending
        header.click()
        prev = page.locator("[data-suggestions-for]").evaluate(
            "tr => tr.previousElementSibling.id"
        )
        assert prev == _entry_row(page, "Rent").get_attribute("id")


def test_suggestion_row_follows_its_entry_when_dragged(page, flask_server, _db_path):
    """A suggestion detail row isn't draggable itself; after a drag,
    row-reorder.js puts it back under its entry. (The reorder POST is
    intercepted so the shared fixture's order stays put.)"""
    _add_rent_charge(_db_path)
    page.route("**/budget/reorder**", lambda r: r.fulfill(status=204, body=""))
    page.goto(f"{flask_server}{_APRIL}&edit=1&group=none")
    rows = page.locator("#budget-table tr[data-reorder-index]")
    rent = _entry_row(page, "Rent")
    rent_id = rent.get_attribute("id")
    rent.locator(".drag-handle").drag_to(rows.nth(0))
    page.wait_for_function(
        f"document.querySelector('#budget-table tr[data-reorder-index]').id === '{rent_id}'"
    )
    prev = page.locator("[data-suggestions-for]").evaluate(
        "tr => tr.previousElementSibling.id"
    )
    assert prev == rent_id


# --- Recurring view (issues #100, #143) --------------------------------------


def _add_monthly_series(db_path, merchant="StreamCo", amount="-12.99") -> None:
    """Six monthly charges ending a few days ago, on Main Checking."""
    from datetime import timedelta

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    today = datetime.now().astimezone().date()
    with engine.connect() as conn:
        acct = conn.execute(
            select(holdings.c.id)
            .join(snapshots, holdings.c.snapshot_id == snapshots.c.id)
            .where(
                snapshots.c.name == "test_finances",
                holdings.c.name == "Main Checking",
            )
        ).scalar_one()
        imp = conn.execute(
            insert(imports).values(
                account_id=acct,
                holding_group="cash",
                filename="e2e-recurring.ofx",
                file_hash=uuid.uuid4().hex,
                status="confirmed",
            )
        ).inserted_primary_key[0]
        for i in range(6):
            conn.execute(
                insert(transactions).values(
                    import_id=imp,
                    account_id=acct,
                    date=today - timedelta(days=30 * i + 3),
                    amount=amount,
                    raw_description=merchant.upper(),
                    normalized_merchant=merchant,
                    fingerprint=uuid.uuid4().hex,
                )
            )
        conn.commit()
    engine.dispose()


def _recurring_row(page, merchant="StreamCo", status=None):
    attr = f"[data-status='{status}']" if status else ""
    return page.locator(f"#recurring-table tr[data-recurring='{merchant}']{attr}")


def test_view_toggle_opens_recurring(page, flask_server, _db_path):
    _add_monthly_series(_db_path)
    page.goto(f"{flask_server}{_APRIL}")
    page.click("[data-budget-views] a[data-view='recurring']")
    page.wait_for_url("**view=recurring**")
    assert "ym=2026-04" in page.url
    assert _recurring_row(page).get_by_text("Not in budget").is_visible()
    page.click("[data-budget-views] a[data-view='plan']")
    page.locator("#budget-table").wait_for()
    assert page.locator("text=Apr 2026").first.is_visible()


def test_add_recurring_to_budget(page, flask_server, _db_path):
    _add_monthly_series(_db_path)
    page.goto(f"{flask_server}{_URL}?view=recurring")
    assert page.locator("[data-recurring-add]").count() == 0
    enable_edit_mode(page)
    _recurring_row(page).locator("[data-recurring-add]").click()
    _recurring_row(page, status="linked").wait_for()
    assert _recurring_row(page).get_by_text("In budget").is_visible()
    page.goto(f"{flask_server}{_URL}")
    assert _entry_row(page, "StreamCo").is_visible()


def test_dismiss_recurring(page, flask_server, _db_path):
    _add_monthly_series(_db_path)
    page.goto(f"{flask_server}{_URL}?view=recurring&edit=1")
    _recurring_row(page).locator("[data-recurring-dismiss]").click()
    _recurring_row(page).wait_for(state="detached")
    assert "No recurring charges found" in page.locator("#recurring-table").inner_text()


# --- Dropped requests (PR preview 429s) ---------------------------------------


def _fail_recurring_posts(page, action, status, times):
    left = {"n": times}

    def handle(route):
        if left["n"] > 0:
            left["n"] -= 1
            return route.fulfill(status=status, body="")
        return route.continue_()

    page.route(f"**/budget/recurring/{action}?*", handle)


def test_dropped_action_is_retried(page, flask_server, _db_path):
    """The PR preview answers overlapping requests with 429: the tap must
    still land rather than silently doing nothing."""
    _add_monthly_series(_db_path)
    page.goto(f"{flask_server}{_URL}?view=recurring&edit=1")
    _fail_recurring_posts(page, "add", 429, 1)
    _recurring_row(page).locator("[data-recurring-add]").click()
    _recurring_row(page, status="linked").wait_for()
    assert page.locator("#request-error").is_hidden()


def test_failed_action_says_so(page, flask_server, _db_path):
    _add_monthly_series(_db_path)
    page.goto(f"{flask_server}{_URL}?view=recurring&edit=1")
    _fail_recurring_posts(page, "dismiss", 500, 99)
    _recurring_row(page).locator("[data-recurring-dismiss]").click()
    page.locator("#request-error").wait_for(state="visible")
    assert "nothing changed" in page.locator("#request-error").inner_text()
    assert _recurring_row(page).is_visible()


@pytest.mark.parametrize("query", ["", "?view=recurring"])
@pytest.mark.parametrize("width", PHONE_WIDTHS)
def test_budget_controls_layout_on_phones(page, flask_server, width, query):
    """Pager row above (Recurring keeps the row, without a pager); the Plan /
    Recurring toggle, Filters trigger and Reset share the bar's one row."""
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{flask_server}{_URL}{query}")
    assert_controls_layout(page, width)
    assert page.locator("[data-budget-views]").is_visible()
