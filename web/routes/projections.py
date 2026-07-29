"""Projections page: accounts × months ending-balance grid with a line chart.

Finances-style view (explicit filename argument); horizon and estimator are
query params (?months=N&estimate=1) so states are linkable.
"""

import math
from decimal import Decimal

from flask import Blueprint, current_app, render_template, request

from fintrack.core.formatting import fmt_money
from fintrack.projections import engine
from web.routes.common import get_common_context, validate_snapshot

projections_bp = Blueprint("projections", __name__)

# Chart geometry: a proper plot area inside gutters for the y-axis value labels
# (left) and the month labels (bottom).
CHART_WIDTH = 720
CHART_HEIGHT = 240
CHART_PAD_L = 52
CHART_PAD_R = 14
CHART_PAD_T = 12
CHART_PAD_B = 26

MONTH_CHOICES = (6, 12, 24, 36)

_PROJECTION_GROUPS = [
    ("cash", "Cash"),
    ("credit", "Credit Cards"),
    ("loan", "Loans"),
]


def _row_group_key(row: dict) -> str:
    if row.get("_source") == "debt":
        return "loan"
    t = row["account"].get("type")
    if t == "credit_card":
        return "credit"
    if t == "loan":
        return "loan"
    return "cash"


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
            fmt_money(sum((r["balances"][mi] for r in rows), Decimal("0")))
            for mi in range(n)
        ]
        groups.append(
            {"key": key, "label": label, "rows": rows, "subtotals": subtotals}
        )
    return groups


def _fmt_compact(v: float) -> str:
    """Short money label for axis ticks / tooltips, e.g. $9k, -$1.2k, $500."""
    neg = v < 0
    a = abs(v)
    if a >= 1000:
        thousands = a / 1000
        body = f"${thousands:.0f}k" if a % 1000 < 1 else f"${thousands:.1f}k"
    else:
        body = f"${a:.0f}"
    return f"-{body}" if neg else body


def _nice_ticks(lo: float, hi: float, count: int = 4) -> list[float]:
    """Evenly-spaced, human-friendly y-axis tick values spanning [lo, hi]."""
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / count
    mag = 10 ** math.floor(math.log10(raw))
    norm = raw / mag
    nice = 1 if norm < 1.5 else 2 if norm < 3 else 5 if norm < 7 else 10
    step = nice * mag
    start = math.ceil(lo / step) * step
    ticks = []
    v = start
    while v <= hi + step * 1e-6:
        ticks.append(round(v, 6))
        v += step
    return ticks


def _chart(result) -> dict | None:
    """Precompute an SVG line chart (gridlines, points, hover bands) for the
    liquid-total and net-worth series. Dependency-free — the template renders
    the returned coordinates directly."""
    liquid = [float(v) for v in result["liquid"]]
    net_worth = [float(v) for v in result["net_worth"]]
    labels = [m["label"] for m in result["months"]]
    n = len(liquid)
    if n < 2:
        return None
    lo = min(min(liquid), min(net_worth), 0.0)
    hi = max(max(liquid), max(net_worth), 0.0)
    if hi == lo:
        hi += 1.0
    plot_w = CHART_WIDTH - CHART_PAD_L - CHART_PAD_R
    plot_h = CHART_HEIGHT - CHART_PAD_T - CHART_PAD_B

    def x(i):
        return CHART_PAD_L + plot_w * i / (n - 1)

    def y(v):
        return CHART_PAD_T + plot_h * (hi - v) / (hi - lo)

    def polyline(series):
        return " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(series))

    def dots(series):
        return [
            {"x": round(x(i), 1), "y": round(y(v), 1)} for i, v in enumerate(series)
        ]

    gridlines = [
        {"y": round(y(t), 1), "label": _fmt_compact(t), "is_zero": abs(t) < 1e-9}
        for t in _nice_ticks(lo, hi)
    ]
    # A wide, invisible hover band per month carries a native tooltip with both
    # series' values for that month; a guide line + emphasized dots are drawn on
    # top so the point is easy to read on hover.
    band_w = plot_w / (n - 1)
    bands = [
        {
            "x": round(x(i) - band_w / 2, 1),
            "w": round(band_w, 1),
            "cx": round(x(i), 1),
            "label": labels[i],
            "liquid": _fmt_compact(liquid[i]),
            "net_worth": _fmt_compact(net_worth[i]),
        }
        for i in range(n)
    ]
    step = max(1, (n - 1) // 6)
    ticks = [{"x": round(x(i), 1), "label": labels[i]} for i in range(0, n, step)]
    return {
        "width": CHART_WIDTH,
        "height": CHART_HEIGHT,
        "pad_l": CHART_PAD_L,
        "pad_t": CHART_PAD_T,
        "plot_w": plot_w,
        "plot_h": plot_h,
        "plot_bottom": CHART_PAD_T + plot_h,
        "liquid_points": polyline(liquid),
        "net_worth_points": polyline(net_worth),
        "liquid_dots": dots(liquid),
        "net_worth_dots": dots(net_worth),
        "gridlines": gridlines,
        "bands": bands,
        "ticks": ticks,
        "zero_y": round(y(0.0), 1),
        "show_zero": lo < 0.0 < hi,
    }


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
        chart=_chart(result),
        months=months,
        month_choices=MONTH_CHOICES,
        estimate=estimate,
        open_groups=open_groups,
        **context,
    )
