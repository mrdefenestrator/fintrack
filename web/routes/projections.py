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

Filters and grouping (#144): Holdings' Type / Balance / Institution filters
(web/routes/holding_filters.py; ?type= ?balance= ?institution=) narrow the
rows and band subtotals, and a "Group by" choice (?group=) re-bands them by
holding group (the default), liquidity tier, institution or type — or None, a
flat sheet. The Unassigned flows match no filter, so they hide while one is
on. The Liquid / Net worth footer and the summary line stay snapshot-wide.
"""

from decimal import Decimal
from typing import Any

from flask import Blueprint, current_app, render_template, request, url_for

from fintrack.core.types import HOLDING_TYPE_LABELS, HOLDING_TYPE_VALUES
from fintrack.networth.calculations import account_tier, asset_tier
from fintrack.projections import engine, timeline
from web.routes.common import get_common_context, validate_snapshot
from web.routes.grouped_sheet import (
    CATEGORY_TONES,
    NEUTRAL_TONE,
    Band,
    Column,
    GroupBy,
    band_key,
    flat_sheet,
    group_columns,
    selected_group,
    unique_keys,
)
from web.routes.holding_filters import read_filters

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

# "Group by" choices (#144); the first (Holdings' bands) is the default.
PROJECTION_GROUPS = [
    GroupBy("holding", "Holding group"),
    GroupBy("tier", "Liquidity"),
    GroupBy("institution", "Institution"),
    GroupBy("type", "Type"),
    GroupBy("none", "None"),
]
_TIERS = [
    ("liquid", "Liquid", "emerald"),
    ("semi_liquid", "Semi-liquid", "cyan"),
    ("illiquid", "Illiquid", "indigo"),
]
_NO_INSTITUTION = "(no institution)"
# Page state the filter form carries along as hidden inputs (its own dropdowns
# supply group and the filters).
_CARRIED = ("from", "months", "estimate", "view", "edit", "month")
_NO_TYPE = "(no type)"

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


def _row_meta(row: dict) -> dict:
    """What the filters and groupings read off a timeline row: its holding
    group, type, institution, balance side (by group, as on Holdings: cards
    and loans are liabilities even at zero) and liquidity tier."""
    acc = row["account"]
    group = _row_group_key(row)
    is_account = row.get("_source") is None
    return {
        "group_key": group,
        "type_value": acc.get("type") or ("loan" if group == "loan" else None),
        "institution": acc.get("institution") or "",
        "balance_side": "liability" if group in ("credit", "loan") else "asset",
        "tier": account_tier(acc) if is_account else asset_tier(acc),
    }


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


def _bands(rows: list[dict], group: str) -> list[Band]:
    """Split filtered rows into the bands of `group` (not "none"), in band
    order, dropping empty ones. Rows keep their timeline order in a band."""

    def split(key_of, order, label_of, tone_of, prefix):
        by: dict[str, list] = {}
        for r in rows:
            by.setdefault(key_of(r), []).append(r)
        present = [k for k in order if k in by]
        keys = unique_keys(band_key(prefix, k) for k in present)
        return [
            Band(bk, label_of(k), tone_of(k), by[k])
            for k, bk in zip(present, keys, strict=True)
        ]

    if group == "tier":
        labels = {k: (lbl, tone) for k, lbl, tone in _TIERS}
        return split(
            lambda r: r["tier"],
            [k for k, _, _ in _TIERS],
            lambda k: labels[k][0],
            lambda k: labels[k][1],
            "tier",
        )
    if group == "institution":
        named = sorted({r["institution"] for r in rows if r["institution"]})
        tones = {
            k: CATEGORY_TONES[i % len(CATEGORY_TONES)] for i, k in enumerate(named)
        }
        return split(
            lambda r: r["institution"],
            [*named, ""],
            lambda k: k or _NO_INSTITUTION,
            lambda k: tones.get(k, NEUTRAL_TONE),
            "inst",
        )
    if group == "type":
        # Tones index the full type list, so a type keeps its color whatever
        # else is shown.
        tones = {
            k: CATEGORY_TONES[i % len(CATEGORY_TONES)]
            for i, k in enumerate(HOLDING_TYPE_VALUES)
        }
        return split(
            lambda r: r["type_value"] or "",
            [*HOLDING_TYPE_VALUES, ""],
            lambda k: HOLDING_TYPE_LABELS.get(k, _NO_TYPE),
            lambda k: tones.get(k, NEUTRAL_TONE),
            "type",
        )
    # Holding group: Holdings' own keys, labels and tones.
    return [
        Band(key, label, tone, [r for r in rows if r["group_key"] == key])
        for key, label, tone in _PROJECTION_GROUPS
        if any(r["group_key"] == key for r in rows)
    ]


def _group_rows(tl: dict, view: str, group: str, flt) -> list[dict]:
    """The grid's bodies: one band per `group` value over the rows `flt`
    keeps (plus Unassigned while no filter is on), or for "none" a single
    flat body."""
    rows = flt.apply({**r, **_row_meta(r)} for r in tl["rows"])
    shown = [
        {**r, "cells": _display(tl, r["cells"], r["start"], r["opening"], view)}
        for r in rows
    ]
    unassigned = None
    if tl["unassigned"] is not None and not flt.active:
        # Budget flows with no account set: not a holding, but money the
        # projection moves, so it gets its own (neutral) band. It has no type,
        # institution or side, so any filter hides it.
        u = tl["unassigned"]
        cells = _display(tl, u["cells"], Decimal(0), u["opening"], view)
        unassigned = {"account": None, "cells": cells}

    cols = _columns(tl, view)
    if group == "none":
        flat_rows = shown + ([unassigned] if unassigned else [])
        if not flat_rows:
            return []
        return [{**flat_sheet("projections", cols, _TOOLTIPS), "rows": flat_rows}]

    by_id = {id(r): s for r, s in zip(rows, shown, strict=True)}
    groups = [
        _band(b.key, b.label, b.tone, [by_id[id(r)] for r in b.rows])
        for b in _bands(rows, group)
    ]
    if unassigned:
        groups.append(_band(*_UNASSIGNED, [unassigned]))
    meta = group_columns(cols, _TOOLTIPS)
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
    flt = read_filters((_row_meta(r) for r in tl["rows"]), request.args)
    group = selected_group(PROJECTION_GROUPS)

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
        "group": None if group == PROJECTION_GROUPS[0].key else group,
        **flt.params(),
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
        groups=_group_rows(tl, view, group, flt),
        group=group,
        group_options=PROJECTION_GROUPS,
        filters_active=flt.active,
        hidden_params={
            k: v for k, v in params.items() if v is not None and k in _CARRIED
        },
        **flt.context(),
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
