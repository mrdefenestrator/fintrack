"""Pure helpers for level-payment, fully amortizing loans."""

import calendar
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext


def _decimal(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _valid_inputs(principal, annual_rate, term_months) -> tuple | None:
    try:
        p = _decimal(principal)
        rate = _decimal(annual_rate)
        term = int(term_months)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if p <= 0 or rate < 0 or term <= 0:
        return None
    return p, rate, term


def scheduled_payment(principal, annual_rate, term_months) -> Decimal | None:
    """Return the level monthly principal-and-interest payment."""
    values = _valid_inputs(principal, annual_rate, term_months)
    if values is None:
        return None
    principal, annual_rate, term_months = values
    monthly_rate = annual_rate / Decimal(12)
    if monthly_rate == 0:
        return principal / term_months
    with localcontext() as ctx:
        ctx.prec = 40
        growth = (Decimal(1) + monthly_rate) ** term_months
        return principal * monthly_rate * growth / (growth - 1)


def payoff_progress(original_principal, current_balance) -> Decimal | None:
    """Return a fraction from zero through one representing principal paid."""
    try:
        original = _decimal(original_principal)
        current = _decimal(current_balance)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if original <= 0:
        return None
    return min(Decimal(1), max(Decimal(0), (original - current) / original))


def _payment_date(year: int, month: int, due_day: int | None) -> date:
    last_day = calendar.monthrange(year, month)[1]
    day = last_day if due_day is None else min(due_day, last_day)
    return date(year, month, day)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def payment_dates(
    origination_date: date,
    count: int,
    due_day_of_month: int | None = None,
):
    """Yield scheduled dates, beginning with the first due date after origination."""
    if due_day_of_month is not None and not 1 <= due_day_of_month <= 31:
        return
    year, month = origination_date.year, origination_date.month
    candidate = _payment_date(year, month, due_day_of_month)
    if candidate <= origination_date:
        year, month = _next_month(year, month)
    for _ in range(max(0, count)):
        yield _payment_date(year, month, due_day_of_month)
        year, month = _next_month(year, month)


def expected_balance(
    original_principal,
    annual_rate,
    term_months,
    origination_date: date,
    as_of: date,
    due_day_of_month: int | None = None,
) -> Decimal | None:
    """Return scheduled principal remaining after payments due by ``as_of``."""
    values = _valid_inputs(original_principal, annual_rate, term_months)
    if (
        values is None
        or not isinstance(origination_date, date)
        or not isinstance(as_of, date)
    ):
        return None
    principal, annual_rate, term_months = values
    dates = list(payment_dates(origination_date, term_months, due_day_of_month))
    if len(dates) != term_months:
        return None
    paid_periods = sum(due_date <= as_of for due_date in dates)
    if paid_periods >= term_months:
        return Decimal(0)
    payment = scheduled_payment(principal, annual_rate, term_months)
    monthly_rate = annual_rate / Decimal(12)
    if monthly_rate == 0:
        return max(Decimal(0), principal - payment * paid_periods)
    with localcontext() as ctx:
        ctx.prec = 40
        growth = (Decimal(1) + monthly_rate) ** paid_periods
        balance = principal * growth - payment * (growth - 1) / monthly_rate
    return max(Decimal(0), balance)


def projected_payoff_date(
    current_balance,
    annual_rate,
    payment,
    as_of: date,
    due_day_of_month: int | None = None,
) -> date | None:
    """Return the scheduled date on which a current balance reaches zero."""
    try:
        balance = _decimal(current_balance)
        annual_rate = _decimal(annual_rate)
        payment = _decimal(payment)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if balance <= 0:
        return as_of
    if annual_rate < 0 or payment <= 0:
        return None
    monthly_rate = annual_rate / Decimal(12)
    if payment <= balance * monthly_rate:
        return None
    dates = payment_dates(as_of, 1200, due_day_of_month)
    for due_date in dates:
        balance = balance * (Decimal(1) + monthly_rate) - payment
        if balance <= 0:
            return due_date
    return None
