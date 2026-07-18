"""Tests for the Trends page window paging (QA issue 5).

Covers the pure date-window helpers in web/routes/trends.py (parsing,
clamping, month shifting) plus the /trends and /trends/detail routes'
handling of the `end=YYYY-MM` query param: default-to-latest, malformed
input, past windows, and forward-paging back to "latest".
"""

from datetime import date
from decimal import Decimal

import pytest

from fintrack.ledger.repository.accounts import add_account
from fintrack.ledger.repository.imports import (
    confirm_import,
    create_import,
    insert_transactions,
)
from fintrack.snapshots.repository import get_snapshot_id
from web.routes.trends import (
    _parse_end_param,
    _period_range,
    _period_stride,
    _resolve_window_end,
    _shift_month,
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
    end, year, month, is_latest = _resolve_window_end("2024-02", today)
    assert end == date(2024, 2, 29)
    assert is_latest is False


# ---------------------------------------------------------------------------
# _period_stride — each period pages by one whole window
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "period,stride",
    [
        ("quarterly", 3),
        ("ytd", 12),
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


def test_period_range_ytd_latest_is_year_to_date():
    today = date(2026, 7, 17)
    assert _period_range("ytd", today, is_latest=True) == (date(2026, 1, 1), today)


def test_period_range_ytd_past_is_full_calendar_year():
    # Paging YTD back one page lands on the full prior calendar year — this is
    # what the removed "Last Year" button used to show.
    anchor = date(2025, 7, 31)
    assert _period_range("ytd", anchor, is_latest=False) == (
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
    # At the latest window: no "Latest" reset button, and the forward (next)
    # arrow is disabled rather than a live link. (The back arrow still carries
    # an end anchor for the previous window — that's expected.)
    assert "Latest" not in html
    assert 'aria-disabled="true"' in html


def test_trends_malformed_end_falls_back_to_latest(client):
    response = client.get("/s/ledger/trends?end=not-a-date")
    assert response.status_code == 200
    html = response.data.decode()
    assert "Latest" not in html


def test_trends_future_end_falls_back_to_latest(client):
    today = date.today()
    future_year = today.year + 1
    response = client.get(f"/s/ledger/trends?end={future_year}-01")
    assert response.status_code == 200
    html = response.data.decode()
    assert "Latest" not in html


def test_trends_past_end_shows_latest_button_and_window_label(client):
    today = date.today()
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
    today = date.today()
    response = client.get(f"/s/ledger/trends?end={today.year:04d}-{today.month:02d}")
    assert response.status_code == 200
    html = response.data.decode()
    assert "Latest" not in html


def test_trends_quarterly_pages_back_by_a_quarter(client):
    """The 'earlier' link steps the quarterly window back one quarter (3 mo)."""
    today = date.today()
    response = client.get("/s/ledger/trends?period=quarterly")
    html = response.data.decode()
    py, pm = today.year, today.month - 3
    while pm <= 0:
        pm += 12
        py -= 1
    assert f"end={py:04d}-{pm:02d}" in html


def test_trends_ytd_pages_back_by_a_calendar_year(client):
    """The 'earlier' link steps the YTD window back one calendar year."""
    today = date.today()
    response = client.get("/s/ledger/trends?period=ytd")
    html = response.data.decode()
    assert f"end={today.year - 1:04d}-{today.month:02d}" in html


def test_trends_trailing12_pages_back_by_a_year(client):
    """The 'earlier' link slides the trailing-12 window back a full year."""
    today = date.today()
    response = client.get("/s/ledger/trends?period=trailing12")
    html = response.data.decode()
    assert f"end={today.year - 1:04d}-{today.month:02d}" in html


def test_trends_preserves_period_param_while_paging(client):
    today = date.today()
    past_year, past_month = today.year, today.month - 1
    if past_month <= 0:
        past_month += 12
        past_year -= 1
    response = client.get(
        f"/s/ledger/trends?period=ytd&end={past_year:04d}-{past_month:02d}"
    )
    assert response.status_code == 200
    html = response.data.decode()
    assert "Year to Date" in html
    # The active preset button should still be YTD (blue highlight class
    # appears alongside the label somewhere in that button's markup).
    assert "bg-blue-500" in html


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

    today = date.today()
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
