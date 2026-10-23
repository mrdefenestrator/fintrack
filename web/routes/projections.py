"""Projections page: headline summary plus an accounts × months grid.

Finances-style view (explicit filename argument); horizon, estimator, and the
grid view are query params (?months=N&estimate=1&view=change) so states are
linkable. The Balances view shows month-end balances after a "Now" column; the
Changes view shows each month's change instead.
"""

from decimal import Decimal

from flask import Blueprint, current_app, render_template, request, url_for

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


def _cells(start: Decimal, series: list[Decimal], view: str) -> list[Decimal]:
    return engine.changes(start, series) if view == "change" else series


def _group_rows(result, view: str) -> list[dict]:
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
        start = sum((r["start"] for r in rows), Decimal(0))
        subtotals = [
            sum((r["balances"][mi] for r in rows), Decimal(0)) for mi in range(n)
        ]
        groups.append(
            {
                "key": key,
                "label": label,
                "rows": [
                    {**r, "cells": _cells(r["start"], r["balances"], view)}
                    for r in rows
                ],
                "start": start,
                "cells": _cells(start, subtotals, view),
            }
        )
    return groups


def _footer(result, view: str) -> dict[str, list[Decimal]]:
    return {
        "unassigned": _cells(Decimal(0), result["unassigned"], view),
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
    open_groups = set(request.args.getlist("open"))

    context = get_common_context(snapshot_id, filename, edit_mode)
    eng = current_app.config["engine"]
    with eng.connect() as conn:
        result = engine.project(
            conn, snapshot_id, months=months, include_estimate=estimate
        )

    params = {
        "months": months,
        "estimate": 1 if estimate else None,
        "view": "change" if view == "change" else None,
        "edit": 1 if edit_mode else None,
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
        proj_url=proj_url,
        open_groups=open_groups,
        **context,
    )
