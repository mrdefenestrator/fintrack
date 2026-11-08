"""Monthly cash flow and savings rate (issue #99), shared by the Trends footer,
the Overview "Cash flow" card, `report trends`, and `status`.

Per month:

- **Income** is the Income category's total.
- **Spending** is every other category except Transfer, flipped to a positive
  outflow. Refunds are negative spending (they sit in the category they
  reverse), not income.
- **Net (kept)** is income minus spending, the same figure as Trends' footer.
- **Savings rate** is net / income; None when income is zero or negative.
- **Budgeted net** is the plan for that month: budget income minus budget
  expenses falling in the month (Transfer-category entries left out).

Averages follow Trends (#103): completed months with data only, the month in
progress left out. The average savings rate is average net / average income,
not a mean of monthly rates (a low-income month shouldn't swing it).
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from typing import Any, TypedDict

from sqlalchemy.engine import Connection

from fintrack.budget.recurrence import budget_entry_in_month
from fintrack.budget.repository import get_budget_entries
from fintrack.ledger.repository.aggregations import get_monthly_totals_range
from fintrack.ledger.trends import MonthKey, averaging_months, monthly_average

INCOME_CATEGORY = "Income"
# Categories that are neither income nor spending: moving money between your
# own accounts (card payments, savings sweeps) would otherwise count twice.
EXCLUDED_CATEGORIES = frozenset({"Transfer"})


class CashFlowFigures(TypedDict):
    income: float | None
    spending: float | None
    net: float | None
    rate: float | None
    budgeted_net: float | None


class CashFlowMonth(CashFlowFigures):
    year: int
    month: int


class CashFlow(TypedDict):
    months: list[CashFlowMonth]
    average: CashFlowFigures
    avg_month_count: int
    in_progress: MonthKey | None


def savings_rate(net: float | None, income: float | None) -> float | None:
    """Share of income kept, in %; None when there's no positive income."""
    if net is None or income is None or income <= 0:
        return None
    return net / income * 100


def budgeted_net_in_month(
    budget: Iterable[dict[str, Any]], year: int, month: int
) -> float:
    """Planned income minus planned expenses falling in this month."""
    total = 0.0
    for entry in budget:
        if entry.get("category") in EXCLUDED_CATEGORIES:
            continue
        amount = float(budget_entry_in_month(entry, year, month))
        if entry.get("kind") == "income":
            total += amount
        elif entry.get("kind") == "expense":
            total -= amount
    return total


def compute_cash_flow(
    monthly_rows: Iterable[dict[str, Any]],
    months: list[MonthKey],
    in_progress: MonthKey | None = None,
    budget: list[dict[str, Any]] | None = None,
) -> CashFlow:
    """Cash flow for each of `months` from `get_monthly_totals_range` rows.

    `in_progress` is the partial current month (shown, but kept out of the
    averages). Without `budget`, budgeted net is None throughout.
    """
    income: dict[MonthKey, float] = defaultdict(float)
    spending: dict[MonthKey, float] = defaultdict(float)
    for row in monthly_rows:
        cat = row["category"]
        if cat in EXCLUDED_CATEGORIES:
            continue
        key = (row["year"], row["month"])
        if cat == INCOME_CATEGORY:
            income[key] += float(row["total"])
        else:
            spending[key] -= float(row["total"])

    net = {m: income.get(m, 0.0) - spending.get(m, 0.0) for m in months}
    planned = (
        {m: budgeted_net_in_month(budget, *m) for m in months}
        if budget is not None
        else None
    )

    rows: list[CashFlowMonth] = []
    for m in months:
        inc = income.get(m, 0.0)
        rows.append(
            {
                "year": m[0],
                "month": m[1],
                "income": inc,
                "spending": spending.get(m, 0.0),
                "net": net[m],
                "rate": savings_rate(net[m], inc),
                "budgeted_net": planned[m] if planned is not None else None,
            }
        )

    avg_months = averaging_months(months, in_progress)
    avg_income = monthly_average(income, avg_months)
    avg_net = monthly_average(net, avg_months)
    average: CashFlowFigures = {
        "income": avg_income,
        "spending": monthly_average(spending, avg_months),
        "net": avg_net,
        "rate": savings_rate(avg_net, avg_income),
        "budgeted_net": (
            monthly_average(planned, avg_months) if planned is not None else None
        ),
    }
    return {
        "months": rows,
        "average": average,
        "avg_month_count": len(avg_months),
        "in_progress": in_progress if in_progress in months else None,
    }


def get_cash_flow(
    conn: Connection,
    snapshot_id: int,
    *,
    start: date,
    end: date,
    today: date,
    with_budget: bool = True,
) -> CashFlow:
    """Cash flow for the months with data in [start, end]. The current month
    counts as in progress when the window reaches it."""
    monthly = get_monthly_totals_range(
        conn, start_date=start, end_date=end, snapshot_id=snapshot_id
    )
    months = sorted({(r["year"], r["month"]) for r in monthly})
    budget = get_budget_entries(conn, snapshot_id) if with_budget else None
    current = (today.year, today.month)
    return compute_cash_flow(monthly, months, current if end >= today else None, budget)


def trailing_months_start(today: date, months: int) -> date:
    """First day of the month `months - 1` months before `today`'s month, so
    [start, today] spans `months` calendar months (Trends' Trailing 12)."""
    idx = today.year * 12 + today.month - 1 - (months - 1)
    return date(idx // 12, idx % 12 + 1, 1)
