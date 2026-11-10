"""Projections page: one timeline of actual past and projected future months.

Finances-style view (explicit filename argument). Query params keep every state
linkable: ?from=YYYY-MM (window start; default the current month), ?months=N
(window size: 6 / 12 / 24), ?view=change, ?estimate=1, and ?month=YYYY-MM to
open a projected month's breakdown above the grid.

Months before the current one show actual month-end values (balance_history);
the current month on shows the projection, tinted, with a "Now" column (current
balances) at the divider when the window spans today. See
fintrack/projections/timeline.py for how cells are filled.

The grid is a grouped sheet (web/routes/grouped_sheet.py, shared with Holdings
and Budget): one always-open band per holding group — the same group keys and
tones as Holdings — each with its own sortable header, plus an Unassigned band
for budget flows with no account, and the Liquid / Net worth totals pinned in
the shared footer.
"""

from decimal import Decimal
from typing import Any

from flask import Blueprint, current_app, render_template, request, url_for

from fintrack.projections import engine, timeline
from web.routes.common import get_common_context, validate_snapshot
from web.routes.grouped_sheet import Column, group_columns

projections_bp = Blueprint("projections", __name__)

MONTH_CHOICES = timeline.WINDOW_CHOICES

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


def _column_kinds(tl: dict, view: str) -> list[str]:
    """One kind per data column (after Account): past / now / future."""
    kinds = []
    for m in tl["months"]:
        if m["current"] and view != "change":
            kinds.append("now")
        kinds.append("past" if m["past"] else "future")
    return kinds


def _columns(tl: dict, view: str) -> list[Column]:
    """Account, then one column per month, with Now (Balances view only) just
    before the current month."""
    cols: list[Column] = [("account", "Account", False, 1)]
    for m in tl["months"]:
        if m["current"] and view != "change":
            cols.append(("now", "Now", True, 1))
        cols.append((f"m{m['key']}", m["label"], True, 1))
    return cols


def _display(
    tl: dict, cells: list[dict], now: Decimal, opening: Any, view: str
) -> list[dict]:
    """A row's cells as rendered: balances with Now spliced in before the
    current month, or (Changes view) each month's change."""
    if view == "change":
        deltas = timeline.changes(cells, opening, tl["months"], now)
        return [
            {**c, "value": d, "carried": False}
            for c, d in zip(cells, deltas, strict=True)
        ]
    out = []
    for c, m in zip(cells, tl["months"], strict=True):
        if m["current"]:
            out.append(_cell(now))
        out.append(c)
    return out


def _cell(value, **extra) -> dict:
    return {"value": value, "carried": False, "below": False, "missing": 0, **extra}


def _subtotals(rows: list[dict]) -> list[dict]:
    """Per-column band subtotal over the rows' displayed cells (balances or
    changes): the sum of the values present, with how many rows have none
    (blank when none do)."""
    out = []
    for j in range(len(rows[0]["cells"])):
        values = [r["cells"][j]["value"] for r in rows]
        present = [v for v in values if v is not None]
        out.append(
            _cell(
                sum(present, Decimal(0)) if present else None,
                missing=len(values) - len(present) if present else 0,
            )
        )
    return out


def _band(key: str, label: str, tone: str, rows: list[dict]) -> dict:
    return {
        "key": key,
        "label": label,
        "tone": tone,
        "rows": rows,
        "cells": _subtotals(rows),
    }


def _group_rows(tl: dict, view: str) -> list[dict]:
    grouped: dict[str, list] = {key: [] for key, _, _ in _PROJECTION_GROUPS}
    for row in tl["rows"]:
        key = _row_group_key(row)
        if key in grouped:
            grouped[key].append(row)

    groups = []
    for key, label, tone in _PROJECTION_GROUPS:
        rows = grouped[key]
        if not rows:
            continue
        band_rows = [
            {**r, "cells": _display(tl, r["cells"], r["start"], r["opening"], view)}
            for r in rows
        ]
        groups.append(_band(key, label, tone, band_rows))

    if tl["unassigned"] is not None:
        # Budget flows with no account set: not a holding, but money the
        # projection moves, so it gets its own (neutral) band.
        u = tl["unassigned"]
        cells = _display(tl, u["cells"], Decimal(0), u["opening"], view)
        groups.append(_band(*_UNASSIGNED, [{"account": None, "cells": cells}]))

    meta = group_columns(_columns(tl, view), _TOOLTIPS)
    return [{**g, **meta} for g in groups]


def _footer(tl: dict, view: str) -> dict[str, list[dict]]:
    """Liquid / Net worth: totals with Now, or (Changes view) the like-for-like
    change totals (``timeline.change_totals``)."""
    if view == "change":
        return {
            key: [_cell(c["value"], missing=c["missing"]) for c in cells]
            for key, cells in timeline.change_totals(tl).items()
        }
    proj = tl["projection"]
    return {
        key: _display(
            tl,
            [_cell(t["value"], missing=t["missing"]) for t in tl[key]],
            proj[f"start_{key}"],
            None,
            view,
        )
        for key in ("liquid", "net_worth")
    }


@projections_bp.route("/s/<string:filename>/projections")
def projections_view(filename):
    snapshot_id = validate_snapshot(filename)
    edit_mode = request.args.get("edit") == "1"
    months = request.args.get("months", engine.DEFAULT_MONTHS, type=int)
    months = engine.clamp_months(months)
    start = timeline.parse_month(request.args.get("from"))
    estimate = request.args.get("estimate") == "1"
    view = "change" if request.args.get("view") == "change" else "balances"

    context = get_common_context(snapshot_id, filename, edit_mode)
    eng = current_app.config["engine"]
    with eng.connect() as conn:
        tl = timeline.build_timeline(
            conn, snapshot_id, start=start, months=months, include_estimate=estimate
        )
    result = tl["projection"]
    at_now = tl["start"] == tl["current"]

    # ?month=YYYY-MM: a projected month in the window (past months have no
    # breakdown yet).
    open_month = next(
        (
            m
            for m in tl["months"]
            if m["key"] == request.args.get("month") and m["index"] is not None
        ),
        None,
    )
    breakdown = (
        engine.month_breakdown(result, open_month["index"]) if open_month else None
    )
    future_months = [m for m in tl["months"] if m["index"] is not None]
    if open_month:
        pos = future_months.index(open_month)
        breakdown_prev = future_months[pos - 1]["key"] if pos > 0 else None
        breakdown_next = (
            future_months[pos + 1]["key"] if pos + 1 < len(future_months) else None
        )
    else:
        breakdown_prev = breakdown_next = None

    params = {
        "from": None if at_now else timeline.month_key(tl["start"]),
        "months": months,
        "estimate": 1 if estimate else None,
        "view": "change" if view == "change" else None,
        "edit": 1 if edit_mode else None,
        "month": open_month["key"] if open_month else None,
    }

    def proj_url(**overrides):
        """This page's URL with the current options, some overridden. Pass
        from_ for the window start (``from`` is a keyword)."""
        if "from_" in overrides:
            overrides["from"] = overrides.pop("from_")
        return url_for(
            "projections.projections_view",
            filename=filename,
            **{**params, **overrides},
        )

    def window_url(ym):
        start_key = None if ym == tl["current"] else timeline.month_key(ym)
        # Paging away closes a breakdown that would leave the window.
        return proj_url(from_=start_key, month=None)

    return render_template(
        "projections.html",
        active_tab="projections",
        tl=tl,
        result=result,
        groups=_group_rows(tl, view),
        footer=_footer(tl, view),
        col_kinds=_column_kinds(tl, view),
        summary=tl["summary"],
        months=months,
        month_choices=MONTH_CHOICES,
        estimate=estimate,
        view=view,
        open_month=open_month,
        breakdown=breakdown,
        breakdown_prev=breakdown_prev,
        breakdown_next=breakdown_next,
        at_now=at_now,
        prev_url=window_url(tl["prev_start"]) if tl["can_go_back"] else None,
        next_url=window_url(tl["next_start"]),
        now_url=window_url(tl["current"]),
        proj_url=proj_url,
        **context,
    )
