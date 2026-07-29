"""Tests for pure loan amortization helpers."""

from datetime import date
from decimal import Decimal

import pytest

from fintrack.networth.amortization import (
    expected_balance,
    payment_dates,
    payoff_progress,
    projected_payoff_date,
    scheduled_payment,
)


def test_scheduled_payment_zero_rate():
    assert scheduled_payment(1200, 0, 12) == Decimal("100")


def test_scheduled_payment_standard_loan():
    payment = scheduled_payment(300000, Decimal("0.06"), 360)
    assert payment.quantize(Decimal("0.01")) == Decimal("1798.65")


def test_payment_dates_use_due_day_and_clamp_short_months():
    assert list(payment_dates(date(2026, 1, 10), 3, 31)) == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
    ]


def test_payment_dates_default_to_month_end():
    assert list(payment_dates(date(2026, 1, 31), 2)) == [
        date(2026, 2, 28),
        date(2026, 3, 31),
    ]


def test_expected_balance_counts_only_due_payments():
    before_first = expected_balance(
        1200, 0, 12, date(2026, 1, 5), date(2026, 1, 14), 15
    )
    after_first = expected_balance(1200, 0, 12, date(2026, 1, 5), date(2026, 1, 15), 15)
    assert before_first == Decimal("1200")
    assert after_first == Decimal("1100")


def test_expected_balance_mid_schedule():
    balance = expected_balance(
        10000, Decimal("0.06"), 12, date(2026, 1, 1), date(2026, 6, 30), 15
    )
    assert balance.quantize(Decimal("0.01")) == Decimal("5074.81")


def test_payoff_progress_clamps_and_guards():
    assert payoff_progress(10000, 7500) == Decimal("0.25")
    assert payoff_progress(10000, 12000) == Decimal("0")
    assert payoff_progress(10000, -1) == Decimal("1")
    assert payoff_progress(None, 100) is None


def test_projected_payoff_date_zero_rate():
    assert projected_payoff_date(300, 0, 100, date(2026, 1, 10), 15) == date(
        2026, 3, 15
    )


def test_projected_payoff_rejects_negative_amortization():
    assert projected_payoff_date(10000, Decimal("0.12"), 100, date.today()) is None


@pytest.mark.parametrize(
    "args",
    [
        (None, Decimal("0.05"), 12),
        (1000, Decimal("-0.01"), 12),
        (1000, Decimal("0.05"), 0),
    ],
)
def test_scheduled_payment_invalid_inputs(args):
    assert scheduled_payment(*args) is None
