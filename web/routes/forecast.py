"""Forecast page: budget vs. actual spending by category.

Shows trailing 3-month actual spending averages alongside the budgeted
monthly amount for every category, with a delta column highlighting
over/underspend.  The Projections page uses the unbudgeted slice of this
data to optionally fold estimated unscheduled spend into the balance forecast.
"""

from datetime import date
from decimal import Decimal

from flask import Blueprint, current_app, g, render_template, request

from fintrack.budget.recurrence import amount_annual
from fintrack.budget.repository import get_budget_entries
from fintrack.ledger.repository.aggregations import get_rolling_average
from fintrack.projections.estimators import EXCLUDED_CATEGORIES
from web.routes.common import get_common_context, snapshot_scoped

bp = snapshot_scoped(Blueprint("forecast", __name__, url_prefix="/s/<filename>"))

_ZERO = Decimal("0")


def _budgeted_monthly_by_category(
    budget: list[dict],
) -> tuple[dict[str, Decimal], dict[str, str]]:
    """Sum of annualized-then-divided-by-12 amounts, keyed by category.

    Returns (totals, kinds) where totals are unsigned monthly sums and kinds
    maps each category to its budget kind ('income' or 'expense').
    """
    totals: dict[str, Decimal] = {}
    kinds: dict[str, str] = {}
    for entry in budget:
        cat = entry.get("category")
        kind = entry.get("kind")
        if not cat or kind not in ("income", "expense"):
            continue
        monthly = amount_annual(entry) / Decimal(12)
        totals[cat] = totals.get(cat, _ZERO) + monthly
        kinds[cat] = kind
    return totals, kinds


@bp.route("/forecast")
def forecast_view():
    edit_mode = request.args.get("edit") == "1"
    context = get_common_context(g.snapshot_id, g.filename, edit_mode)
    today = date.today()

    eng = current_app.config["engine"]
    with eng.connect() as conn:
        budget = get_budget_entries(conn, g.snapshot_id)
        all_averages = get_rolling_average(
            conn,
            year=today.year,
            month=today.month,
            months_back=3,
            snapshot_id=g.snapshot_id,
        )

    budgeted, budget_kinds = _budgeted_monthly_by_category(budget)

    all_categories = sorted(
        (set(all_averages.keys()) | set(budgeted.keys())) - EXCLUDED_CATEGORIES
    )

    rows = []
    actual_total = _ZERO
    budgeted_total = _ZERO
    for cat in all_categories:
        actual = all_averages.get(cat, _ZERO)
        bgt = budgeted.get(cat, _ZERO)
        kind = budget_kinds.get(cat)
        bgt_display = -bgt if kind == "expense" else bgt if kind == "income" else _ZERO
        is_budgeted = cat in budgeted
        delta = actual - bgt_display if is_budgeted else _ZERO
        rows.append(
            {
                "category": cat,
                "actual": actual,
                "budgeted": bgt_display,
                "delta": delta,
                "is_budgeted": is_budgeted,
            }
        )
        actual_total += actual
        budgeted_total += bgt_display

    rows.sort(key=lambda r: r["actual"])

    return render_template(
        "forecast.html",
        active_tab="forecast",
        rows=rows,
        actual_total=actual_total,
        budgeted_total=budgeted_total,
        delta_total=actual_total - budgeted_total,
        **context,
    )
