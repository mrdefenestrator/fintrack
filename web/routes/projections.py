"""Projections page: headline summary plus an accounts × months grid.

Finances-style view (explicit filename argument); horizon and estimator are
query params (?months=N&estimate=1) so states are linkable.
"""

from decimal import Decimal

from flask import Blueprint, current_app, render_template, request

from fintrack.core.formatting import fmt_money
from fintrack.projections import engine
from web.routes.common import get_common_context, validate_snapshot

projections_bp = Blueprint("projections", __name__)

MONTH_CHOICES = (6, 12, 24, 36)

_PROJECTION_GROUPS = [
    ("cash", "Cash"),
    ("credit", "Credit Cards"),
    ("loan", "Loans"),
    ("asset", "Assets"),
]


def _row_group_key(row: dict) -> str:
    if row.get("_source") == "debt":
        return "loan"
    if row.get("_source") == "asset":
        return "asset"
    # Account rows are only cash or credit cards; loans come through as debt
    # entries (the _source == "debt" branch above), not account rows.
    return "credit" if row["account"].get("type") == "credit_card" else "cash"


def _group_rows(result) -> list[dict]:
    grouped: dict[str, list] = {key: [] for key, _ in _PROJECTION_GROUPS}
    for row in result["rows"]:
        key = _row_group_key(row)
        if key in grouped:
            grouped[key].append(row)

    groups = []
    for key, label in _PROJECTION_GROUPS:
        rows = grouped[key]
        if not rows:
            continue
        n = len(result["months"])
        subtotals = [
            sum((r["balances"][mi] for r in rows), Decimal(0)) for mi in range(n)
        ]
        groups.append(
            {
                "key": key,
                "label": label,
                "rows": rows,
                "start": sum((r["start"] for r in rows), Decimal(0)),
                "subtotals": [fmt_money(v) for v in subtotals],
            }
        )
    return groups


@projections_bp.route("/s/<string:filename>/projections")
def projections_view(filename):
    snapshot_id = validate_snapshot(filename)
    edit_mode = request.args.get("edit") == "1"
    months = request.args.get("months", engine.DEFAULT_MONTHS, type=int)
    months = engine.clamp_months(months)
    estimate = request.args.get("estimate") == "1"
    open_groups = set(request.args.getlist("open"))

    context = get_common_context(snapshot_id, filename, edit_mode)
    eng = current_app.config["engine"]
    with eng.connect() as conn:
        result = engine.project(
            conn, snapshot_id, months=months, include_estimate=estimate
        )

    return render_template(
        "projections.html",
        active_tab="projections",
        result=result,
        groups=_group_rows(result),
        summary=engine.summarize(result),
        months=months,
        month_choices=MONTH_CHOICES,
        estimate=estimate,
        open_groups=open_groups,
        **context,
    )
