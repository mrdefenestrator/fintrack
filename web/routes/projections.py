"""Projections page: accounts × months ending-balance grid with a line chart.

Finances-style view (explicit filename argument); horizon and estimator are
query params (?months=N&estimate=1) so states are linkable.
"""

from flask import Blueprint, current_app, render_template, request

from fintrack.projections import engine
from web.routes.common import get_common_context, validate_snapshot

projections_bp = Blueprint("projections", __name__)

CHART_WIDTH = 720
CHART_HEIGHT = 220
CHART_MARGIN = 12

MONTH_CHOICES = (6, 12, 24, 36)


def _chart(result) -> dict | None:
    """Precompute SVG polyline points for the liquid / net-worth series."""
    liquid = [float(v) for v in result["liquid"]]
    net_worth = [float(v) for v in result["net_worth"]]
    n = len(liquid)
    if n < 2:
        return None
    lo = min(min(liquid), min(net_worth), 0.0)
    hi = max(max(liquid), max(net_worth), 0.0)
    if hi == lo:
        hi += 1.0
    plot_w = CHART_WIDTH - 2 * CHART_MARGIN
    plot_h = CHART_HEIGHT - 2 * CHART_MARGIN

    def x(i):
        return CHART_MARGIN + plot_w * i / (n - 1)

    def y(v):
        return CHART_MARGIN + plot_h * (hi - v) / (hi - lo)

    def points(series):
        return " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(series))

    labels = [m["label"] for m in result["months"]]
    step = max(1, (n - 1) // 6)
    ticks = [{"x": x(i), "label": labels[i]} for i in range(0, n, step)]
    return {
        "width": CHART_WIDTH,
        "height": CHART_HEIGHT,
        "liquid_points": points(liquid),
        "net_worth_points": points(net_worth),
        "zero_y": y(0.0),
        "show_zero": lo < 0.0 < hi,
        "hi": hi,
        "lo": lo,
        "hi_y": y(hi),
        "lo_y": y(lo),
        "ticks": ticks,
    }


@projections_bp.route("/s/<string:filename>/projections")
def projections_view(filename):
    snapshot_id = validate_snapshot(filename)
    edit_mode = request.args.get("edit") == "1"
    months = request.args.get("months", engine.DEFAULT_MONTHS, type=int)
    months = engine.clamp_months(months)
    estimate = request.args.get("estimate") == "1"

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
        chart=_chart(result),
        months=months,
        month_choices=MONTH_CHOICES,
        estimate=estimate,
        **context,
    )
