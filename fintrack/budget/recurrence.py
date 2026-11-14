"""Recurrence engine for budget entries.

How much of a scheduled income/expense entry lands in a given month, in two
flavors: full-month occurrence (`budget_entry_in_month`) and remainder-of-
month proration from a given day (`subtotal_remainder_of_month`). Extracted
from the finances calculations module so multi-month projections and the
current-month key numbers share one implementation.

The biweekly approximation is kept from finances: biweekly entries count as
exactly 2 pay periods per month (26/year), so months with a third payday are
understated and the annual total is slightly conservative. Weekly entries
(issue #100) use the exact average instead, 52/12 occurrences per month, and
prorate the current month by days remaining.

Budget history (issue #136): an entry is in the plan only from its
``startMonth`` through its ``endMonth`` (ISO dates, the 1st of a month; either
may be absent = unbounded), and its amount/schedule may change over time —
``_versions`` lists each version with the month it took effect (``from``; the
base version's is None). The per-month functions here resolve the version in
effect for the month they're asked about (``entry_for_month``), so a
multi-month caller (projections, cash flow, the Budget sheet's actuals) reads each
month's own plan. An entry's flat fields are the version in effect for the
month the repository loaded it for; ``amount_annual`` reads those.
"""

import calendar
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

_ZERO = Decimal(0)
# Average weekly occurrences per month (52 weeks / 12 months).
WEEKS_PER_MONTH = Decimal(52) / Decimal(12)


def money(x: Any) -> Decimal:
    """Normalize a money value (Decimal from the DB, or int/float/str from
    in-memory data and tests) to Decimal. None becomes 0.

    Goes through str() so float inputs don't carry binary-repr noise
    (Decimal(str(1234.56)) == Decimal('1234.56')), matching the spending
    project's Decimal(str(...)) convention.
    """
    if x is None:
        return _ZERO
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def month_sequence(year: int, month: int, count: int) -> list[tuple[int, int]]:
    """`count` consecutive (year, month) pairs starting at (year, month),
    rolling over year boundaries."""
    return [_add_months(year, month, i) for i in range(count)]


def _add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    total = (year * 12) + (month - 1) + offset
    return total // 12, (total % 12) + 1


def quarter_months(start_month: int) -> set:
    """Months (1–12) in which a quarterly item occurs: start_month and every 3 months.

    Examples:
        quarter_months(1) -> {1, 4, 7, 10}  # Jan, Apr, Jul, Oct
        quarter_months(2) -> {2, 5, 8, 11}  # Feb, May, Aug, Nov
        quarter_months(3) -> {3, 6, 9, 12}  # Mar, Jun, Sep, Dec
    """
    # Convert to 0-based, add offsets, convert back to 1-based
    return {((start_month - 1 + offset) % 12) + 1 for offset in (0, 3, 6, 9)}


def semiannual_other_month(month: int) -> int:
    """Other month (1–12) for a semiannual item (6 months later)."""
    return (month + 5) % 12 + 1


# The fields a budget entry version carries (issue #136); everything else
# (kind, description, category, auto account) applies to every month.
VERSIONED_FIELDS = (
    "amount",
    "recurrence",
    "date",
    "dayOfMonth",
    "month",
    "dayOfYear",
    "continuous",
)


def as_month(value: Any) -> date | None:
    """The 1st of the month of a date or ISO date string (None stays None)."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return value.replace(day=1)


def entry_active(entry: dict[str, Any], year: int, month: int) -> bool:
    """Whether the entry is in the plan for this month (between its start and
    end months, both inclusive; an absent bound is unbounded)."""
    key = date(year, month, 1)
    start = as_month(entry.get("startMonth"))
    end = as_month(entry.get("endMonth"))
    return (start is None or start <= key) and (end is None or key <= end)


def version_in_effect(entry: dict[str, Any], year: int, month: int) -> dict[str, Any]:
    """The entry's versioned fields as of this month: the latest version that
    took effect on or before it (the base version before the first change).
    An entry without ``_versions`` is its own only version."""
    versions = entry.get("_versions")
    if not versions:
        return {f: entry.get(f) for f in VERSIONED_FIELDS}
    key = date(year, month, 1)
    chosen = versions[0]
    for v in versions[1:]:
        if as_month(v["from"]) <= key:
            chosen = v
        else:
            break
    return {f: chosen.get(f) for f in VERSIONED_FIELDS}


def entry_for_month(
    entry: dict[str, Any], year: int, month: int
) -> dict[str, Any] | None:
    """The entry as planned for this month, or None when it isn't in the plan
    then. Returns ``entry`` itself when nothing differs (the common case)."""
    if not entry_active(entry, year, month):
        return None
    if not entry.get("_versions"):
        return entry
    fields = version_in_effect(entry, year, month)
    if all(entry.get(f) == fields[f] for f in VERSIONED_FIELDS):
        return entry
    out = {k: v for k, v in entry.items() if k not in VERSIONED_FIELDS}
    out.update({k: v for k, v in fields.items() if v is not None})
    return out


def budget_entry_in_month(
    entry: dict[str, Any], year: int, month: int, day: int | None = None
) -> Decimal:
    """Amount of this income/expense entry that falls in the given month (0 or full amount).
    For continuous monthly entries, day is used to prorate by proportion of month remaining.
    The month's own version applies; an entry not in that month's plan is 0.
    """
    entry = entry_for_month(entry, year, month)
    if entry is None:
        return _ZERO
    rec = entry.get("recurrence", "")
    amount = money(entry.get("amount"))
    if rec == "monthly":
        if entry.get("continuous") and day is not None:
            days_in_month = calendar.monthrange(year, month)[1]
            days_remaining = max(0, days_in_month - day)
            return amount * Decimal(days_remaining) / Decimal(days_in_month)
        return amount
    if rec == "one_time":
        d = entry.get("date")
        if not d:
            return _ZERO
        try:
            parsed = date.fromisoformat(d)
            if parsed.year == year and parsed.month == month:
                return amount
        except (TypeError, ValueError):
            pass
        return _ZERO
    if rec == "annual":
        if entry.get("month") == month:
            return amount
        return _ZERO
    if rec == "quarterly":
        m = entry.get("month")
        if m is not None and month in quarter_months(m):
            return amount
        return _ZERO
    if rec == "semiannual":
        m = entry.get("month")
        if m is not None and (month == m or month == semiannual_other_month(m)):
            return amount
        return _ZERO
    if rec == "biweekly":
        return amount * 2  # approx 2 pay periods per month
    if rec == "weekly":
        return amount * WEEKS_PER_MONTH
    return _ZERO


def amount_annual(entry: dict[str, Any]) -> Decimal:
    """Annualized amount for this entry (for budget --annual). One-time returns amount as-is."""
    rec = entry.get("recurrence", "")
    amount = money(entry.get("amount"))
    if rec == "monthly":
        return amount * 12
    if rec == "biweekly":
        return amount * 26  # ~26 pay periods per year
    if rec == "weekly":
        return amount * 52
    if rec == "annual":
        return amount
    if rec == "quarterly":
        return amount * 4
    if rec == "semiannual":
        return amount * 2
    if rec == "one_time":
        return amount
    return _ZERO


def subtotal_remainder_of_month(
    entry: dict[str, Any], year: int, month: int, day: int
) -> Decimal:
    """Expected amount for the remainder of the month for this entry (non-negative).
    Used for the income/expenses table Subtotal column. See DESIGN.md for rules.
    day=0 means start of month (full month remaining).
    The month's own version applies; an entry not in that month's plan is 0.
    """
    entry = entry_for_month(entry, year, month)
    if entry is None:
        return _ZERO
    rec = entry.get("recurrence", "")
    amount = money(entry.get("amount"))
    day_effective = max(
        1, day
    )  # for date() and days_remaining; day<dom uses day (0<dom ok)
    if rec == "monthly":
        if entry.get("continuous"):
            days_in_month = calendar.monthrange(year, month)[1]
            days_remaining = (
                max(0, days_in_month - day_effective) if day > 0 else days_in_month
            )
            return amount * Decimal(days_remaining) / Decimal(days_in_month)
        # Not continuous: full amount if we haven't reached dayOfMonth yet
        dom = entry.get("dayOfMonth")
        if dom is None:
            return amount  # no day specified, treat as full month
        return amount if day < dom else _ZERO
    if rec == "one_time":
        d = entry.get("date")
        if not d:
            return _ZERO
        try:
            parsed = date.fromisoformat(d)
            if (
                parsed.year == year
                and parsed.month == month
                and parsed >= date(year, month, day_effective)
            ):
                return amount
        except (TypeError, ValueError):
            pass
        return _ZERO
    if rec == "annual":
        if entry.get("month") != month:
            return _ZERO
        dom = entry.get("dayOfMonth")
        doy = entry.get("dayOfYear")
        day_val = dom if dom is not None else doy
        if day_val is None:
            return amount
        return amount if day < day_val else _ZERO
    if rec == "quarterly":
        m = entry.get("month")
        if m is None or month not in quarter_months(m):
            return _ZERO
        dom = entry.get("dayOfMonth")
        if dom is None:
            return amount
        return amount if day < dom else _ZERO
    if rec == "semiannual":
        m = entry.get("month")
        if m is None or (month != m and month != semiannual_other_month(m)):
            return _ZERO
        dom = entry.get("dayOfMonth")
        if dom is None:
            return amount
        return amount if day < dom else _ZERO
    if rec == "biweekly":
        return amount * 2  # approx 2 pay periods for remainder of month
    if rec == "weekly":
        days_in_month = calendar.monthrange(year, month)[1]
        days_remaining = (
            max(0, days_in_month - day_effective) if day > 0 else days_in_month
        )
        return amount * WEEKS_PER_MONTH * Decimal(days_remaining) / days_in_month
    return _ZERO


def iter_month_amounts(
    entry: dict[str, Any],
    start: date,
    months: int,
) -> Iterator[Decimal]:
    """Entry amounts for `months` consecutive months starting at `start`'s
    month: the first month prorated from `start.day` (remainder-of-month
    semantics), later months as full-month occurrences."""
    for i, (year, month) in enumerate(month_sequence(start.year, start.month, months)):
        if i == 0:
            yield subtotal_remainder_of_month(entry, year, month, start.day)
        else:
            yield budget_entry_in_month(entry, year, month)
