"""Projections page: a one-line summary plus an accounts × months grid.

Finances-style view (explicit filename argument); horizon, estimator, and the
grid view are query params (?months=N&estimate=1&view=change) so states are
linkable; ?month=I (0-based) opens that month's breakdown above the grid.
The Balances view shows month-end balances after a "Now" column; the Changes
view shows each month's change instead.

The grid is a grouped sheet (web/routes/grouped_sheet.py, shared with Holdings
and Budget): one always-open band per holding group — the same group keys and
tones as Holdings — each with its own sortable header, plus an Unassigned band
for budget flows with no account, and the Liquid / Net worth totals pinned in
the shared footer.
"""

from decimal import Decimal

from flask import Blueprint, current_app, render_template, request, url_for

from fintrack.projections import engine
from web.routes.common import get_common_context, validate_snapshot
from web.routes.grouped_sheet import Column, group_columns

projections_bp = Blueprint("projections", __name__)

MONTH_CHOICES = (6, 12, 24, 36)

# (key, label, tone): the Holdings bands and palette (cool = owned, warm = owed),
# so a holding reads the same on both sheets.
_PROJECTION_GROUPS = [
    ("cash", "Cash", "emerald"),
    ("credit", "Credit Cards", "rose"),
    ("loan", "Loans", "amber"),
    ("asset", "Assets", "blue"),
]
_UNASSIGNED = ("unassigned", "Unassigned", "slate")

_TOOLTIPS = {
    "account": "Account or holding name",
    "now": "Current balance (start of projection)",
}


def _row_group_key(row: dict) -> str:
    if row.get("_source") == "debt":
        return "loan"
    if row.get("_source") == "asset":
        return "asset"
    # Account rows are only cash or credit cards; loans come through as debt
    # entries (the _source == "debt" branch above), not account rows.
    return "credit" if row["account"].get("type") == "credit_card" else "cash"


def _cells(start: Decimal, series: list[Decimal], view: str) -> list[Decimal]:
    return engine.changes(start, series) if view == "change" else series


def _columns(month_labels: list[str], view: str) -> list[Column]:
    """Account, then (Balances view only) Now, then one column per month."""
    cols: list[Column] = [("account", "Account", False, 1)]
    if view != "change":
        cols.append(("now", "Now", True, 1))
    cols += [(f"m{i}", label, True, 1) for i, label in enumerate(month_labels)]
    return cols


def _band(key: str, label: str, tone: str, rows: list[dict], start, series, view):
    return {
        "key": key,
        "label": label,
        "tone": tone,
        "rows": rows,
        "start": start,
        "cells": _cells(start, series, view),
    }


def _group_rows(result, view: str) -> list[dict]:
    grouped: dict[str, list] = {key: [] for key, _, _ in _PROJECTION_GROUPS}
    for row in result["rows"]:
        key = _row_group_key(row)
        if key in grouped:
            grouped[key].append(row)

    n = len(result["months"])
    groups = []
    for key, label, tone in _PROJECTION_GROUPS:
        rows = grouped[key]
        if not rows:
            continue
        start = sum((r["start"] for r in rows), Decimal(0))
        subtotals = [
            sum((r["balances"][mi] for r in rows), Decimal(0)) for mi in range(n)
        ]
        band_rows = [
            {**r, "cells": _cells(r["start"], r["balances"], view)} for r in rows
        ]
        groups.append(_band(key, label, tone, band_rows, start, subtotals, view))

    if result["has_unassigned"]:
        # Budget flows with no account set: not a holding, but money the
        # projection moves, so it gets its own (neutral) band.
        cells = _cells(Decimal(0), result["unassigned"], view)
        row = {
            "account": None,
            "start": Decimal(0),
            "cells": cells,
            "below": [False] * n,
        }
        groups.append(
            _band(*_UNASSIGNED, [row], Decimal(0), result["unassigned"], view)
        )

    meta = group_columns(
        _columns([m["label"] for m in result["months"]], view), _TOOLTIPS
    )
    return [{**g, **meta} for g in groups]


def _footer(result, view: str) -> dict[str, list[Decimal]]:
    return {
        "liquid": _cells(result["start_liquid"], result["liquid"], view),
        "net_worth": _cells(result["start_net_worth"], result["net_worth"], view),
    }


@projections_bp.route("/s/<string:filename>/projections")
def projections_view(filename):
    snapshot_id = validate_snapshot(filename)
    edit_mode = request.args.get("edit") == "1"
    months = request.args.get("months", engine.DEFAULT_MONTHS, type=int)
    months = engine.clamp_months(months)
    estimate = request.args.get("estimate") == "1"
    view = "change" if request.args.get("view") == "change" else "balances"
    month_index = request.args.get("month", type=int)

    context = get_common_context(snapshot_id, filename, edit_mode)
    eng = current_app.config["engine"]
    with eng.connect() as conn:
        result = engine.project(
            conn, snapshot_id, months=months, include_estimate=estimate
        )

    if month_index is not None and not 0 <= month_index < len(result["months"]):
        month_index = None
    breakdown = (
        engine.month_breakdown(result, month_index) if month_index is not None else None
    )

    params = {
        "months": months,
        "estimate": 1 if estimate else None,
        "view": "change" if view == "change" else None,
        "edit": 1 if edit_mode else None,
        "month": month_index,
    }

    def proj_url(**overrides):
        """This page's URL with the current options, some overridden."""
        return url_for(
            "projections.projections_view",
            filename=filename,
            **{**params, **overrides},
        )

    return render_template(
        "projections.html",
        active_tab="projections",
        result=result,
        groups=_group_rows(result, view),
        footer=_footer(result, view),
        summary=engine.summarize(result),
        months=months,
        month_choices=MONTH_CHOICES,
        estimate=estimate,
        view=view,
        month_index=month_index,
        breakdown=breakdown,
        proj_url=proj_url,
        **context,
    )
