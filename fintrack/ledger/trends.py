"""Trend math shared by the Trends page and `report trends` (issue #103).

Averages count only *completed* months that have data. Dividing by every
calendar month in the window understates anyone with less history than the
window (6 months of $1,800 rent in a 12-month window is not $900/mo), and the
month in progress is partial, so it would drag the average down.
"""

MonthKey = tuple[int, int]


def averaging_months(
    months: list[MonthKey], in_progress: MonthKey | None
) -> list[MonthKey]:
    """The months an average divides by: those with data (`months`, the keys
    seen anywhere in the snapshot's window, so a one-off category isn't
    inflated), minus the month still in progress."""
    return [m for m in months if m != in_progress]


def monthly_average(
    values: dict[MonthKey, float], months: list[MonthKey]
) -> float | None:
    """Mean of `values` over `months` (missing months count as 0); None when
    there are no completed months to average."""
    if not months:
        return None
    return sum(values.get(m, 0.0) for m in months) / len(months)


def pct_change(last: float | None, avg: float | None) -> float | None:
    """How far the last completed month's size is from the average size, in %."""
    if last is None or not avg:
        return None
    return (abs(last) - abs(avg)) / abs(avg) * 100
