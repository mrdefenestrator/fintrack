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
    LIQUIDITY_TIERS,
)
from fintrack.networth import calculations

from .common import get_common_context, validate_snapshot

holdings_bp = Blueprint("holdings", __name__, url_prefix="/s")

# Combined {value: label} lookup for account and asset-entry types.
_TYPE_LABELS: dict[str, str] = {
    **dict(ACCOUNT_TYPE_OPTIONS),
    **dict(ASSET_TYPE_OPTIONS),
}

TIER_LABELS: dict[str, str] = {
    "liquid": "Liquid",
    "semi_liquid": "Semi-liquid",
    "illiquid": "Illiquid",
}

# The two "balance side" filter buckets, keyed by the sign of a holding's
# contribution (assets add to net worth, liabilities subtract).
_BALANCE_LABELS: dict[str, str] = {"asset": "Assets", "liability": "Liabilities"}

# Column order for the holdings sheet. Amount/Equity/LTV are right-aligned.
_HEADERS = ["Kind", "Institution", "Name", "Type", "Tier", "Amount", "Equity", "LTV"]
_RIGHT_ALIGN_COLS = [5, 6, 7]


def _type_label(value: str | None) -> str:
    if not value:
        return ""
    return _TYPE_LABELS.get(value, value)


def _fmt_ltv(ltv: Decimal | None) -> str:
    return f"{ltv * 100:.1f}%" if ltv is not None else "—"


def _holding(kind_label: str, institution: str, name: str, type_value, tier, amount):
    """Build one holding record: display cells + sort/filter metadata."""
    is_liability = amount < 0
    return {
        "kind": kind_label,
        "institution": institution,
        "name": name,
        "tier": tier,
        "balance_side": "liability" if is_liability else "asset",
        "amount": amount,
        "cells": [
            kind_label,
            institution or "—",
            name or "—",
            _type_label(type_value) or "—",
            TIER_LABELS.get(tier, tier),
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
            calculations.account_tier(a),
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
            calculations.asset_tier(e),
            calculations.asset_contribution(e),
        )
        pair = equity_by_debt.get(id(e))
        if pair is not None:
            row["cells"][6] = fmt_money(pair["equity"])
            row["cells"][7] = _fmt_ltv(pair["ltv"])
        rows.append(row)

    institutions = sorted({r["institution"] for r in rows if r["institution"]})

    # --- filters (multi-select, form-submit; mirrors the Assets sheet) ---
    tier_sel = [t for t in request.args.getlist("tier") if t in LIQUIDITY_TIERS]
    balance_sel = [b for b in request.args.getlist("balance") if b in _BALANCE_LABELS]
    inst_sel = [i for i in request.args.getlist("institution") if i in institutions]

    filtered = rows
    if tier_sel:
        filtered = [r for r in filtered if r["tier"] in tier_sel]
    if balance_sel:
        filtered = [r for r in filtered if r["balance_side"] in balance_sel]
    if inst_sel:
        filtered = [r for r in filtered if r["institution"] in inst_sel]

    # A filter group only "counts" as active when it's a proper subset (matching
    # the Assets sheet: selecting every option is the same as no filter).
    def _active(sel, total):
        return len(sel) if 0 < len(sel) < total else 0

    active_count = (
        _active(tier_sel, len(LIQUIDITY_TIERS))
        + _active(balance_sel, len(_BALANCE_LABELS))
        + _active(inst_sel, len(institutions))
    )

    def _opts(values_labels, selected):
        return [
            {"value": v, "display": lbl, "checked": v in selected}
            for v, lbl in values_labels
        ]

    # Bottom total = sum of the displayed rows (equals net worth with no filter;
    # becomes the tier subtotal when filtered by tier).
    total = sum((r["amount"] for r in filtered), Decimal("0"))
    total_cells = ["Total", "", "", "", "", fmt_money(total), "", ""]

    ctx.update(
        {
            "headers": _HEADERS,
            "right_align_cols": _RIGHT_ALIGN_COLS,
            "holdings_rows": filtered,
            "total_cells": total_cells,
            "active_count": active_count,
            "tier_opts": _opts(
                [(t, TIER_LABELS[t]) for t in LIQUIDITY_TIERS], tier_sel
            ),
            "balance_opts": _opts(list(_BALANCE_LABELS.items()), balance_sel),
            "institution_opts": _opts([(i, i) for i in institutions], inst_sel),
        }
    )
    return render_template("holdings.html", **ctx)
