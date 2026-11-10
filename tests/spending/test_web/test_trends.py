"""Tests for the Trends page window paging (QA issue 5).

Covers the pure date-window helpers in web/routes/trends.py (parsing,
clamping, month shifting) plus the /trends and /trends/detail routes'
handling of the `end=YYYY-MM` query param: default-to-latest, malformed
input, past windows, and forward-paging back to "latest".
"""

import re
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import insert

from fintrack.budget.repository import add_budget_entry
from fintrack.core.models import merchant_cache
from fintrack.ledger.repository.accounts import add_account
from fintrack.ledger.repository.imports import (
    confirm_import,
    create_import,
    insert_transactions,
)
from fintrack.ledger.trends import averaging_months, monthly_average, pct_change
from fintrack.snapshots.repository import get_snapshot_id
from web.routes.trends import (
    _delta_words,
    _parse_end_param,
    _period_range,
    _period_stride,
    _resolve_window_end,
    _shift_month,
    _unusual_tints,
)

# ---------------------------------------------------------------------------
# _parse_end_param
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-07", (2026, 7)),
        ("2000-01", (2000, 1)),
        ("2026-12", (2026, 12)),
    ],
)
def test_parse_end_param_valid(value, expected):
    assert _parse_end_param(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "not-a-date",
        "2026-13",  # month out of range
        "2026-00",  # month out of range
        "2026/07",  # wrong separator
        "26-07",  # wrong year width
        "2026-7",  # month not zero-padded
        "2026-07-15",  # full date, not year-month
    ],
)
def test_parse_end_param_malformed_returns_none(value):
    assert _parse_end_param(value) is None


# ---------------------------------------------------------------------------
# _shift_month
# ---------------------------------------------------------------------------


def test_shift_month_forward_within_year():
    assert _shift_month(2026, 3, 1) == (2026, 4)


def test_shift_month_backward_within_year():
    assert _shift_month(2026, 3, -1) == (2026, 2)


def test_shift_month_forward_crosses_year_boundary():
    assert _shift_month(2026, 12, 1) == (2027, 1)


def test_shift_month_backward_crosses_year_boundary():
    assert _shift_month(2026, 1, -1) == (2025, 12)


# ---------------------------------------------------------------------------
# _resolve_window_end
# ---------------------------------------------------------------------------


def test_resolve_window_end_defaults_to_latest_when_missing():
    today = date(2026, 7, 17)
    end, year, month, is_latest = _resolve_window_end(None, today)
    assert end == today
    assert (year, month) == (2026, 7)
    assert is_latest is True


def test_resolve_window_end_falls_back_to_latest_when_malformed():
    today = date(2026, 7, 17)
    end, year, month, is_latest = _resolve_window_end("garbage", today)
    assert end == today
    assert (year, month) == (2026, 7)
    assert is_latest is True


def test_resolve_window_end_clamps_future_month_to_latest():
    today = date(2026, 7, 17)
    end, year, month, is_latest = _resolve_window_end("2026-08", today)
    assert end == today
    assert (year, month) == (2026, 7)
    assert is_latest is True


def test_resolve_window_end_current_month_treated_as_latest():
    """An explicit end= for the current month behaves exactly like no end=
    at all (uses the real partial-month `today`, not the last calendar day)."""
    today = date(2026, 7, 17)
    end, year, month, is_latest = _resolve_window_end("2026-07", today)
    assert end == today
    assert (year, month) == (2026, 7)
    assert is_latest is True


def test_resolve_window_end_past_month_resolves_to_last_day():
    today = date(2026, 7, 17)
    end, year, month, is_latest = _resolve_window_end("2026-05", today)
    assert end == date(2026, 5, 31)
    assert (year, month) == (2026, 5)
    assert is_latest is False


def test_resolve_window_end_past_month_prior_year():
    today = date(2026, 1, 10)
    end, year, month, is_latest = _resolve_window_end("2025-12", today)
    assert end == date(2025, 12, 31)
    assert (year, month) == (2025, 12)
    assert is_latest is False


def test_resolve_window_end_handles_february_leap_year():
    today = date(2026, 7, 17)
    end, _year, _month, is_latest = _resolve_window_end("2024-02", today)
    assert end == date(2024, 2, 29)
    assert is_latest is False


# ---------------------------------------------------------------------------
# _period_stride — each period pages by one whole window
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "period,stride",
    [
        ("quarterly", 3),
        ("year", 12),
        ("trailing12", 12),
        ("unknown", 12),  # defensive default
    ],
)
def test_period_stride(period, stride):
    assert _period_stride(period) == stride


# ---------------------------------------------------------------------------
# _period_range — latest windows are period-to-date; paged-back windows are
# the full period (full quarter / full calendar year).
# ---------------------------------------------------------------------------


def test_period_range_quarterly_latest_is_partial_to_today():
    today = date(2026, 7, 17)  # Q3, mid-quarter
    assert _period_range("quarterly", today, is_latest=True) == (
        date(2026, 7, 1),
        today,
    )


def test_period_range_quarterly_past_is_full_quarter():
    # Paged back to Q2 (anchor resolved to end-of-month by _resolve_window_end).
    anchor = date(2026, 4, 30)
    assert _period_range("quarterly", anchor, is_latest=False) == (
        date(2026, 4, 1),
        date(2026, 6, 30),
    )


def test_period_range_year_latest_is_year_to_date():
    today = date(2026, 7, 17)
    assert _period_range("year", today, is_latest=True) == (date(2026, 1, 1), today)


def test_period_range_year_past_is_full_calendar_year():
    # Paging Year back one page lands on the full prior calendar year — this is
    # what the removed "Last Year" button used to show.
    anchor = date(2025, 7, 31)
    assert _period_range("year", anchor, is_latest=False) == (
        date(2025, 1, 1),
        date(2025, 12, 31),
    )


def test_period_range_trailing12_is_twelve_months_ending_at_anchor():
    # A fixed 12-month window either way; paging just slides it.
    today = date(2026, 7, 17)
    assert _period_range("trailing12", today, is_latest=True) == (
        date(2025, 8, 1),
        today,
    )
    anchor = date(2025, 7, 31)
    assert _period_range("trailing12", anchor, is_latest=False) == (
        date(2024, 8, 1),
        date(2025, 7, 31),
    )


# ---------------------------------------------------------------------------
# Route-level: /trends default and paging
# ---------------------------------------------------------------------------


def test_trends_default_has_no_end_param_and_is_latest(client):
    response = client.get("/s/ledger/trends")
    assert response.status_code == 200
    html = response.data.decode()
    # At the latest window both forward controls — the next arrow and the
    # "Latest" button — render as disabled spans (title "Already at the latest
    # window") instead of live links, so the pager width doesn't shift when you
    # page away from latest (QA item 20).
    assert html.count('title="Already at the latest window"') == 2


def test_trends_malformed_end_falls_back_to_latest(client):
    response = client.get("/s/ledger/trends?end=not-a-date")
    assert response.status_code == 200
    html = response.data.decode()
    # Fell back to the latest window: forward controls are disabled spans.
    assert html.count('title="Already at the latest window"') == 2


def test_trends_future_end_falls_back_to_latest(client):
    today = datetime.now().astimezone().date()
    future_year = today.year + 1
    response = client.get(f"/s/ledger/trends?end={future_year}-01")
    assert response.status_code == 200
    html = response.data.decode()
    # Fell back to the latest window: forward controls are disabled spans.
    assert html.count('title="Already at the latest window"') == 2


def test_trends_past_end_shows_latest_button_and_window_label(client):
    today = datetime.now().astimezone().date()
    past_year, past_month = today.year, today.month
    # Go back 2 months, wrapping year if needed.
    past_month -= 2
    if past_month <= 0:
        past_month += 12
        past_year -= 1
    response = client.get(f"/s/ledger/trends?end={past_year:04d}-{past_month:02d}")
    assert response.status_code == 200
    html = response.data.decode()
    assert "Latest" in html
    # The window end label should reflect the paged-to month, not today's.
    expected_month_label = date(past_year, past_month, 1).strftime("%b %Y")
    assert expected_month_label in html


def test_trends_paging_forward_to_current_month_reaches_latest(client):
    """Requesting end= for exactly the current year-month collapses to the
    same 'latest' state as no end= at all (no Latest button shown)."""
    today = datetime.now().astimezone().date()
    response = client.get(f"/s/ledger/trends?end={today.year:04d}-{today.month:02d}")
    assert response.status_code == 200
    html = response.data.decode()
    # Fell back to the latest window: forward controls are disabled spans.
    assert html.count('title="Already at the latest window"') == 2


def test_trends_quarterly_pages_back_by_a_quarter(client):
    """The 'earlier' link steps the quarterly window back one quarter (3 mo)."""
    today = datetime.now().astimezone().date()
    response = client.get("/s/ledger/trends?period=quarterly")
    html = response.data.decode()
    py, pm = today.year, today.month - 3
    while pm <= 0:
        pm += 12
        py -= 1
    assert f"end={py:04d}-{pm:02d}" in html


def test_trends_year_pages_back_by_a_calendar_year(client):
    """The 'earlier' link steps the Year window back one calendar year."""
    today = datetime.now().astimezone().date()
    response = client.get("/s/ledger/trends?period=year")
    html = response.data.decode()
    assert f"end={today.year - 1:04d}-{today.month:02d}" in html


def test_trends_trailing12_pages_back_by_a_year(client):
    """The 'earlier' link slides the trailing-12 window back a full year."""
    today = datetime.now().astimezone().date()
    response = client.get("/s/ledger/trends?period=trailing12")
    html = response.data.decode()
    assert f"end={today.year - 1:04d}-{today.month:02d}" in html


def test_trends_preserves_period_param_while_paging(client):
    today = datetime.now().astimezone().date()
    past_year, past_month = today.year, today.month - 1
    if past_month <= 0:
        past_month += 12
        past_year -= 1
    response = client.get(
        f"/s/ledger/trends?period=ytd&end={past_year:04d}-{past_month:02d}"
    )
    assert response.status_code == 200
    html = response.data.decode()
    # The old "ytd" period (links saved before #102) opens Year, still active.
    assert "data-year-review" in html


# ---------------------------------------------------------------------------
# Route-level: paged window actually changes the underlying data query
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_ledger_account(conn):
    """A checking account with one transaction each in two different, known
    months, used to prove that paging the window changes which month's data
    is queried."""
    snapshot_id = get_snapshot_id(conn, "ledger")
    acct_id = add_account(
        conn,
        name="Test Checking",
        institution="Test Bank",
        account_type="checking",
        snapshot_id=snapshot_id,
    )
    imp_id = create_import(
        conn, account_id=acct_id, filename="seed.ofx", file_hash="seedhash"
    )
    confirm_import(conn, imp_id)

    today = datetime.now().astimezone().date()
    old_year, old_month = today.year, today.month - 3
    if old_month <= 0:
        old_month += 12
        old_year -= 1

    insert_transactions(
        conn,
        import_id=imp_id,
        account_id=acct_id,
        transactions_data=[
            {
                "date": date(old_year, old_month, 10),
                "amount": Decimal("-77.00"),
                "raw_description": "OLD MONTH MERCHANT",
                "normalized_merchant": "OLD MONTH MERCHANT",
                "fingerprint": "old-fp",
            },
        ],
    )
    return {"old_year": old_year, "old_month": old_month}


def test_trends_paged_window_shows_data_from_paged_month(client, seeded_ledger_account):
    old_year = seeded_ledger_account["old_year"]
    old_month = seeded_ledger_account["old_month"]

    # trailing12 default (latest) should include the transaction 3 months back.
    response = client.get("/s/ledger/trends?period=trailing12")
    assert "77" in response.data.decode()

    # Paging back further so the trailing12 window ends before that month
    # entirely excludes it. Shift the end 12 months earlier than the seeded
    # transaction so it falls outside even a trailing-12 window.
    far_year, far_month = old_year, old_month - 12
    if far_month <= 0:
        far_month += 12
        far_year -= 1
    response = client.get(
        f"/s/ledger/trends?period=trailing12&end={far_year:04d}-{far_month:02d}"
    )
    html = response.data.decode()
    assert "OLD MONTH MERCHANT" not in html


def test_trends_detail_respects_end_param(client, seeded_ledger_account):
    old_year = seeded_ledger_account["old_year"]
    old_month = seeded_ledger_account["old_month"]
    response = client.get(
        f"/s/ledger/trends/detail?category=Uncategorized&period=trailing12"
        f"&end={old_year:04d}-{old_month:02d}"
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Heat map: "unusual months" (default) vs "size" (issue #108)
# ---------------------------------------------------------------------------


def _cls(tints):
    return [t["cls"] for t in tints]


def test_unusual_leaves_steady_rows_plain():
    """Rent at the same amount every month gets no shading at all."""
    values = [-1800.0] * 5
    tints = _unusual_tints(values, None, is_income=False)
    assert _cls(tints) == [""] * 5


def test_unusual_marks_spending_above_usual_red_and_below_green():
    """Against the median ($49): the $568 spike is red at full strength, $113
    red, and the $10 month green. One spike doesn't drag "usual" up."""
    values = [-568.0, -49.0, -113.0, -49.0, -49.0, -10.0]
    tints = _unusual_tints(values, None, is_income=False)
    assert _cls(tints) == [
        "heat-worse-3",
        "",
        "heat-worse-3",
        "",
        "",
        "heat-better-2",
    ]
    assert "above usual ($49/mo)" in tints[0]["title"]
    assert "below usual" in tints[5]["title"]


def test_unusual_threshold_is_15_percent():
    """Groceries: usual $336. $391 (+16%) and $408 (+21%) flag; $380 (+13%)
    doesn't."""
    values = [-336.0, -408.0, -336.0, -391.0, -336.0, -380.0, -336.0]
    tints = _unusual_tints(values, None, is_income=False)
    assert _cls(tints) == ["", "heat-worse-1", "", "heat-worse-1", "", "", ""]


def test_unusual_needs_a_ten_dollar_difference():
    """$36 -> $44 is 22% but only $8, so it stays plain; $36 -> $47 flags."""
    values = [-36.0, -36.0, -44.0, -36.0, -47.0]
    tints = _unusual_tints(values, None, is_income=False)
    assert _cls(tints) == ["", "", "", "", "heat-worse-1"]


def test_unusual_income_short_is_red_and_extra_is_green():
    values = [5200.0, 5200.0, 2600.0, 5200.0, 7000.0]
    tints = _unusual_tints(values, None, is_income=True)
    assert _cls(tints) == ["", "", "heat-worse-2", "", "heat-better-1"]
    assert "below usual" in tints[2]["title"]


def test_unusual_skips_the_month_in_progress():
    """The current month is partial: never shaded, and left out of "usual"."""
    values = [-100.0, -100.0, -100.0, -500.0]
    tints = _unusual_tints(values, 3, is_income=False)
    assert _cls(tints) == [""] * 4


def test_unusual_marks_one_offs_in_usually_empty_categories():
    values = [0.0, 0.0, -413.0, 0.0, 0.0]
    tints = _unusual_tints(values, None, is_income=False)
    assert _cls(tints) == ["", "", "heat-worse-2", "", ""]
    assert "One-off" in tints[2]["title"]


def test_trends_highlight_is_on_by_default(client):
    html = client.get("/s/ledger/trends").data.decode()
    assert "Highlight unusual months" in html
    # Only the toggle itself (its href + hx-get) links to the off state;
    # period and paging links stay on the default.
    assert html.count("highlight=off") == 2


def test_trends_highlight_off_is_kept_while_paging(client):
    html = client.get("/s/ledger/trends?highlight=off").data.decode()
    # Period presets and earlier/latest arrows keep it; the toggle turns it on.
    assert html.count("highlight=off") >= 5
    assert not re.search(r'class="[^"]*heat-(worse|better)', html)


# ---------------------------------------------------------------------------
# Avg/mo, Change, and Delta wording (issue #103)
# ---------------------------------------------------------------------------


def test_average_divides_by_completed_months_with_data():
    """6 months of $1,800 rent in a 12-month window averages $1,800, not $900;
    the month in progress is left out."""
    months = [(2026, m) for m in range(6, 12)]  # Jun..Nov, Nov in progress
    rent = {m: -1800.0 for m in months[:-1]} | {(2026, 11): -300.0}
    avg_months = averaging_months(months, (2026, 11))
    assert avg_months == months[:-1]
    assert monthly_average(rent, avg_months) == -1800.0


def test_average_counts_empty_months_that_have_other_data():
    """A one-off in a window with data every month isn't inflated: the
    divisor is months with any data, not months this category has data."""
    months = [(2026, m) for m in range(1, 5)]
    assert monthly_average({(2026, 2): -400.0}, months) == -100.0


def test_average_with_no_completed_months_is_none():
    assert averaging_months([(2026, 11)], (2026, 11)) == []
    assert monthly_average({(2026, 11): -50.0}, []) is None


def test_pct_change_compares_size():
    assert pct_change(-150.0, -100.0) == 50.0
    assert pct_change(80.0, 100.0) == -20.0
    assert pct_change(None, -100.0) is None
    assert pct_change(-150.0, None) is None
    assert pct_change(-150.0, 0.0) is None


@pytest.mark.parametrize(
    "delta,kind,word,tone",
    [
        (-8200.0, "income", "short", "bad"),  # $2,600 avg vs $10,800 budget
        (500.0, "income", "ahead", "good"),
        (-300.0, "expense", "over", "bad"),
        (900.0, "expense", "under", "good"),
        (-6195.0, "net", "short", "bad"),
        (0.3, "expense", "on budget", "neutral"),
    ],
)
def test_delta_words_by_row_kind(delta, kind, word, tone):
    assert _delta_words(delta, kind) == {"word": word, "tone": tone}


def test_delta_words_none_without_budget():
    assert _delta_words(None, "expense") is None


@pytest.fixture
def partial_history(conn):
    """Three completed months of $1,800 plus $100 so far this month, in a
    trailing-12 window with no older data."""
    snapshot_id = get_snapshot_id(conn, "ledger")
    acct_id = add_account(
        conn,
        name="History Checking",
        institution="Test Bank",
        account_type="checking",
        snapshot_id=snapshot_id,
    )
    imp_id = create_import(
        conn, account_id=acct_id, filename="hist.ofx", file_hash="histhash"
    )
    confirm_import(conn, imp_id)
    today = datetime.now().astimezone().date()
    rows = []
    for back in (3, 2, 1):
        y, m = today.year, today.month - back
        if m <= 0:
            m += 12
            y -= 1
        rows.append((date(y, m, 5), "-1800.00", f"h{back}"))
    rows.append((date(today.year, today.month, 1), "-100.00", "h0"))
    insert_transactions(
        conn,
        import_id=imp_id,
        account_id=acct_id,
        transactions_data=[
            {
                "date": d,
                "amount": Decimal(amt),
                "raw_description": "LANDLORD",
                "normalized_merchant": "LANDLORD",
                "fingerprint": fp,
            }
            for d, amt, fp in rows
        ],
    )


def _avg_and_change(html):
    """The Avg/mo and Change cells of the Net (kept) footer row."""
    foot = html.split('data-cash-flow="net"')[-1].split("</tr>")[0]
    cells = re.findall(r"<td[^>]*>(.*?)</td>", foot, re.DOTALL)
    text = [re.sub(r"<[^>]+>|\s+", " ", c).strip() for c in cells]
    return text[-4], text[-1]


def test_trends_avg_ignores_empty_window_months_and_month_in_progress(
    client, partial_history
):
    html = client.get("/s/ledger/trends?period=trailing12").data.decode()
    avg, change = _avg_and_change(html)
    # Old rule: ($5,400 + $100) / 12 months = $458, and Change ▼78%.
    assert avg == "$1,800"
    assert "0%" in change
    assert "Average over the 3 completed months with data" in html


@pytest.fixture
def cash_flow_history(conn):
    """Two completed months of income, spending, and a card payment, plus a
    budget, in the ledger snapshot."""
    snapshot_id = get_snapshot_id(conn, "ledger")
    acct_id = add_account(
        conn,
        name="Flow Checking",
        institution="Test Bank",
        account_type="checking",
        snapshot_id=snapshot_id,
    )
    imp_id = create_import(
        conn, account_id=acct_id, filename="flow.ofx", file_hash="flowhash"
    )
    confirm_import(conn, imp_id)
    today = datetime.now().astimezone().date()
    rows = []
    for back in (2, 1):
        y, m = today.year, today.month - back
        if m <= 0:
            m += 12
            y -= 1
        rows += [
            (date(y, m, 1), "4000.00", "PAYROLL", f"p{back}"),
            (date(y, m, 3), "-1000.00", "LANDLORD", f"l{back}"),
            (date(y, m, 20), "-500.00", "CARD AUTOPAY", f"c{back}"),
        ]
    insert_transactions(
        conn,
        import_id=imp_id,
        account_id=acct_id,
        transactions_data=[
            {
                "date": d,
                "amount": Decimal(amt),
                "raw_description": merchant,
                "normalized_merchant": merchant,
                "fingerprint": fp,
            }
            for d, amt, merchant, fp in rows
        ],
    )
    conn.execute(
        insert(merchant_cache),
        [
            {"merchant_name": "PAYROLL", "category": "Income", "source": "manual"},
            {"merchant_name": "LANDLORD", "category": "Housing", "source": "manual"},
            {
                "merchant_name": "CARD AUTOPAY",
                "category": "Transfer",
                "source": "manual",
            },
        ],
    )
    conn.commit()
    add_budget_entry(
        conn,
        snapshot_id,
        {
            "kind": "income",
            "description": "Pay",
            "amount": 3000,
            "recurrence": "monthly",
        },
    )


def _footer_row(html, key):
    row = html.split(f'data-cash-flow="{key}"')[1].split("</tr>")[0]
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
    return [re.sub(r"<[^>]+>|\s+", " ", c).strip() for c in cells]


def test_trends_cash_flow_footer_rows(client, cash_flow_history):
    """Income, Spending, Net (kept), Savings rate, and Budgeted net (#99);
    the card payment is Transfer, so it isn't spending."""
    html = client.get("/s/ledger/trends?period=trailing12").data.decode()
    income = _footer_row(html, "income")
    spending = _footer_row(html, "spending")
    rate = _footer_row(html, "rate")
    planned = _footer_row(html, "budgeted-net")
    assert income[0] == "Income"
    assert income[1:3] == ["$4,000", "$4,000"]
    assert spending[1:3] == ["$1,000", "$1,000"]
    assert rate[1:3] == ["75%", "75%"]
    assert planned[1:3] == ["$3,000", "$3,000"]
    # Avg/mo column follows the months.
    assert income[3] == "$4,000"
    assert rate[3] == "75%"
    assert "Net (kept)" in _footer_row(html, "net")[0]


def test_trends_year_adds_last_year_columns_and_the_review(
    client, seeded_ledger_account
):
    today = datetime.now().astimezone().date()
    html = client.get("/s/ledger/trends?period=year").data.decode()
    assert "data-year-review" in html
    assert f"{today.year} in review" in html
    assert f"vs {today.year - 1}" in html
    # The other periods have neither.
    html = client.get("/s/ledger/trends?period=trailing12").data.decode()
    assert "data-year-review" not in html and f"vs {today.year - 1}" not in html


def test_normalize_period_accepts_the_old_ytd():
    from web.routes.trends import _normalize_period

    assert _normalize_period("ytd", "trailing12") == "year"
    assert _normalize_period(None, "trailing12") == "trailing12"
    assert _normalize_period("quarterly", "year") == "quarterly"
