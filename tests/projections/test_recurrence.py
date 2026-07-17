"""Table-driven tests for the recurrence engine over multi-month windows,
including year rollover."""

from datetime import date
from decimal import Decimal

import pytest

from fintrack.budget.recurrence import (
    budget_entry_in_month,
    iter_month_amounts,
    month_sequence,
    quarter_months,
    semiannual_other_month,
    subtotal_remainder_of_month,
)


def test_month_sequence_year_rollover():
    assert month_sequence(2026, 11, 4) == [
        (2026, 11),
        (2026, 12),
        (2027, 1),
        (2027, 2),
    ]


def test_month_sequence_multi_year():
    seq = month_sequence(2026, 7, 25)
    assert seq[0] == (2026, 7)
    assert seq[5] == (2026, 12)
    assert seq[6] == (2027, 1)
    assert seq[18] == (2028, 1)
    assert seq[-1] == (2028, 7)


def test_quarter_and_semiannual_helpers():
    assert quarter_months(2) == {2, 5, 8, 11}
    assert semiannual_other_month(3) == 9
    assert semiannual_other_month(9) == 3


# Each case: (entry, window start (year, month), window length,
# expected amount per month across the window).
OCCURRENCE_CASES = [
    (
        "monthly",
        {"kind": "expense", "recurrence": "monthly", "amount": 100},
        (2026, 11),
        4,
        [100, 100, 100, 100],
    ),
    (
        "biweekly approximates two per month",
        {"kind": "income", "recurrence": "biweekly", "amount": 50},
        (2026, 11),
        3,
        [100, 100, 100],
    ),
    (
        "annual in Feb recurs across the year boundary",
        {"kind": "expense", "recurrence": "annual", "amount": 1200, "month": 2},
        (2026, 7),
        12,
        [0, 0, 0, 0, 0, 0, 0, 1200, 0, 0, 0, 0],  # Feb 2027 = index 7
    ),
    (
        "quarterly from Jan hits Jan+Apr after rollover",
        {"kind": "expense", "recurrence": "quarterly", "amount": 90, "month": 1},
        (2026, 11),
        6,
        [0, 0, 90, 0, 0, 90],  # Jan 2027, Apr 2027
    ),
    (
        "semiannual Mar/Sep",
        {"kind": "expense", "recurrence": "semiannual", "amount": 400, "month": 3},
        (2026, 7),
        12,
        [0, 0, 400, 0, 0, 0, 0, 0, 400, 0, 0, 0],  # Sep 2026, Mar 2027
    ),
    (
        "one_time next January",
        {
            "kind": "expense",
            "recurrence": "one_time",
            "amount": 250,
            "date": "2027-01-15",
        },
        (2026, 12),
        3,
        [0, 250, 0],
    ),
]


@pytest.mark.parametrize(
    "entry,start,count,expected",
    [c[1:] for c in OCCURRENCE_CASES],
    ids=[c[0] for c in OCCURRENCE_CASES],
)
def test_budget_entry_across_months(entry, start, count, expected):
    amounts = [
        budget_entry_in_month(entry, year, month)
        for year, month in month_sequence(*start, count)
    ]
    assert amounts == [Decimal(e) for e in expected]


def test_iter_month_amounts_prorates_first_month_only():
    # Monthly entry due on the 10th: already passed on the 16th, so month 0
    # contributes nothing; later months contribute in full.
    entry = {
        "kind": "expense",
        "recurrence": "monthly",
        "amount": 80,
        "dayOfMonth": 10,
    }
    amounts = list(iter_month_amounts(entry, date(2026, 7, 16), 3))
    assert amounts == [Decimal("0"), Decimal("80"), Decimal("80")]


def test_iter_month_amounts_continuous_proration():
    # Continuous monthly spend: month 0 gets the remaining 15/31 of July.
    entry = {
        "kind": "expense",
        "recurrence": "monthly",
        "amount": 310,
        "continuous": True,
    }
    amounts = list(iter_month_amounts(entry, date(2026, 7, 16), 2))
    assert amounts[0] == Decimal("310") * 15 / 31
    assert amounts[1] == Decimal("310")


def test_subtotal_remainder_upcoming_day_of_month_counts():
    entry = {
        "kind": "expense",
        "recurrence": "monthly",
        "amount": 60,
        "dayOfMonth": 20,
    }
    assert subtotal_remainder_of_month(entry, 2026, 7, 16) == Decimal("60")
    assert subtotal_remainder_of_month(entry, 2026, 7, 20) == Decimal("0")


def test_calculations_aliases_still_exported():
    """Existing callers import the underscore names from calculations."""
    from fintrack.networth import calculations

    assert calculations._budget_entry_in_month is budget_entry_in_month
    assert calculations._subtotal_remainder_of_month is subtotal_remainder_of_month
