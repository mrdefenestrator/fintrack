"""Forecast page: unbudgeted spending analysis.

Shows trailing category-level spending averages that aren't claimed by any
budget entry — the spending gap between the budget and actual outflows.
Sits between Budget and Projections in the nav; the Projections page uses
the same estimator to optionally fold this total into the balance forecast.
"""

from flask import Blueprint, current_app, g, render_template, request

from fintrack.budget.repository import get_budget_entries
from fintrack.projections.estimators import (
    unscheduled_monthly_total,
    unscheduled_spend_by_category,
)
from web.routes.common import get_common_context, snapshot_scoped

bp = snapshot_scoped(Blueprint("forecast", __name__, url_prefix="/s/<filename>"))


@bp.route("/forecast")
def forecast_view():
    edit_mode = request.args.get("edit") == "1"
    context = get_common_context(g.snapshot_id, g.filename, edit_mode)

    eng = current_app.config["engine"]
    with eng.connect() as conn:
        budget = get_budget_entries(conn, g.snapshot_id)
        by_category = unscheduled_spend_by_category(conn, g.snapshot_id, budget)
        monthly_total = unscheduled_monthly_total(by_category)

    sorted_categories = sorted(by_category.items(), key=lambda kv: kv[1])

    return render_template(
        "forecast.html",
        active_tab="forecast",
        by_category=sorted_categories,
        monthly_total=monthly_total,
        budget_category_count=len({e.get("category") for e in budget if e.get("category")}),
        **context,
    )
