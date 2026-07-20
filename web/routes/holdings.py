"""Holdings blueprint - read-only unified view of accounts + assets/debts.

Lists every holding (account or asset_entries row) together with its liquidity
tier and signed net-worth contribution, reusing the shared spreadsheet chrome
(sheet table, filter bar, total row). Filtering only — editing stays on the
existing Accounts/Assets pages for now.
"""

from decimal import Decimal

from flask import Blueprint, render_template, request

from fintrack.core.formatting import fmt_money
from fintrack.core.types import (
    ACCOUNT_TYPE_OPTIONS,
    ASSET_TYPE_OPTIONS,
)
from fintrack.networth import calculations

from .common import get_common_context, validate_snapshot

holdings_bp = Blueprint("holdings", __name__, url_prefix="/s")

# Canonical, ordered (value, label) list of every holding type — account types
# first, then asset-only types (loan is shared, so it isn't repeated). Drives
# the Type filter's option order and the type-label lookup.
_ALL_TYPE_OPTIONS: list[tuple[str, str]] = ACCOUNT_TYPE_OPTIONS + [
    o for o in ASSET_TYPE_OPTIONS if o[0] not in dict(ACCOUNT_TYPE_OPTIONS)
]
_TYPE_LABELS: dict[str, str] = dict(_ALL_TYPE_OPTIONS)

# The two "balance side" filter buckets, keyed by the sign of a holding's
# contribution (assets add to net worth, liabilities subtract).
_BALANCE_LABELS: dict[str, str] = {"asset": "Assets", "liability": "Liabilities"}

# Column order for the holdings sheet. Amount/Equity/LTV are right-aligned.
# Liquidity tier is intentionally not a column: it is fully derived from type,
# so a Type filter/column already conveys it; tier's real job is the (deferred)
# liquid/investable/net-worth totals, not a per-row label.
_HEADERS = ["Kind", "Institution", "Name", "Type", "Amount", "Equity", "LTV"]
_RIGHT_ALIGN_COLS = [4, 5, 6]


def _type_label(value: str | None) -> str:
    if not value:
        return ""
    return _TYPE_LABELS.get(value, value)


def _fmt_ltv(ltv: Decimal | None) -> str:
    return f"{ltv * 100:.1f}%" if ltv is not None else "—"


def _holding(kind_label: str, institution: str, name: str, type_value, amount):
    """Build one holding record: display cells + sort/filter metadata."""
    is_liability = amount < 0
    return {
        "kind": kind_label,
        "institution": institution,
        "name": name,
        "type_value": type_value,
        "balance_side": "liability" if is_liability else "asset",
        "amount": amount,
        "cells": [
            kind_label,
            institution or "—",
            name or "—",
            _type_label(type_value) or "—",
            fmt_money(amount),
            "—",  # Equity — filled in for secured loan rows below
            "—",  # LTV
        ],
        "accent": "border-l-rose-400" if is_liability else "border-l-emerald-400",
    }


@holdings_bp.route("/<filename>/holdings")
def holdings_view(filename):
    snapshot_id = validate_snapshot(filename)
    edit_mode = request.args.get("edit") == "1"
    ctx = get_common_context(snapshot_id, filename, edit_mode)
    ctx["active_tab"] = "holdings"

    accounts = ctx["accounts"]
    assets = ctx["assets"]

    # Equity/LTV for secured debts, keyed by the debt entry's identity so we can
    # fold it into that loan's row (per design: equity shows on the loan row).
    equity_by_debt = {id(p["debt"]): p for p in calculations.equity_pairs(assets)}

    rows = [
        _holding(
            "Account",
            a.get("institution") or "",
            a.get("name") or "",
            a.get("type"),
            calculations.account_contribution(a),
        )
        for a in accounts
    ]
    for e in assets:
        kind_label = "Debt" if e.get("kind") == "debt" else "Asset"
        row = _holding(
            kind_label,
            e.get("institution") or "",
            e.get("name") or "",
            e.get("type"),
            calculations.asset_contribution(e),
        )
        pair = equity_by_debt.get(id(e))
        if pair is not None:
            row["cells"][5] = fmt_money(pair["equity"])
            row["cells"][6] = _fmt_ltv(pair["ltv"])
        rows.append(row)

    institutions = sorted({r["institution"] for r in rows if r["institution"]})
    # Type filter options: the canonical types actually present, in canonical
    # order (not alphabetical), so the dropdown reads sensibly.
    present_types = {r["type_value"] for r in rows if r["type_value"]}
    type_values_labels = [
        (v, lbl) for v, lbl in _ALL_TYPE_OPTIONS if v in present_types
    ]

    # --- filters (multi-select, form-submit; mirrors the Assets sheet) ---
    type_sel = [t for t in request.args.getlist("type") if t in present_types]
    balance_sel = [b for b in request.args.getlist("balance") if b in _BALANCE_LABELS]
    inst_sel = [i for i in request.args.getlist("institution") if i in institutions]

    filtered = rows
    if type_sel:
        filtered = [r for r in filtered if r["type_value"] in type_sel]
    if balance_sel:
        filtered = [r for r in filtered if r["balance_side"] in balance_sel]
    if inst_sel:
        filtered = [r for r in filtered if r["institution"] in inst_sel]

    # A filter group only "counts" as active when it's a proper subset (matching
    # the Assets sheet: selecting every option is the same as no filter).
    def _active(sel, total):
        return len(sel) if 0 < len(sel) < total else 0

    active_count = (
        _active(type_sel, len(type_values_labels))
        + _active(balance_sel, len(_BALANCE_LABELS))
        + _active(inst_sel, len(institutions))
    )

    def _opts(values_labels, selected):
        return [
            {"value": v, "display": lbl, "checked": v in selected}
            for v, lbl in values_labels
        ]

    # Bottom total = sum of the displayed rows (equals net worth with no filter).
    total = sum((r["amount"] for r in filtered), Decimal("0"))
    total_cells = ["Total", "", "", "", fmt_money(total), "", ""]

    ctx.update(
        {
            "headers": _HEADERS,
            "right_align_cols": _RIGHT_ALIGN_COLS,
            "holdings_rows": filtered,
            "total_cells": total_cells,
            "active_count": active_count,
            "type_opts": _opts(type_values_labels, type_sel),
            "balance_opts": _opts(list(_BALANCE_LABELS.items()), balance_sel),
            "institution_opts": _opts([(i, i) for i in institutions], inst_sel),
        }
    )
    return render_template("holdings.html", **ctx)
