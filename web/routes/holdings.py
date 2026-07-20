"""Holdings blueprint - read-only unified view of accounts + assets/debts.

Lists every holding (account or asset_entries row) together with its
liquidity tier and signed net-worth contribution. Filtering only — editing
stays on the existing Accounts/Assets pages.
"""

from flask import Blueprint, render_template, request, url_for

from fintrack.core.types import (
    ACCOUNT_TYPE_OPTIONS,
    ASSET_TYPE_OPTIONS,
    LIQUIDITY_TIERS,
)
from fintrack.networth import calculations

from .common import get_common_context, validate_snapshot

holdings_bp = Blueprint("holdings", __name__, url_prefix="/s")

# Combined {value: label} lookup for account and asset-entry types, used to
# show a friendly "Type" column across both domains.
_TYPE_LABELS: dict[str, str] = {
    **dict(ACCOUNT_TYPE_OPTIONS),
    **dict(ASSET_TYPE_OPTIONS),
}

# Friendly labels for liquidity tiers.
TIER_LABELS: dict[str, str] = {
    "liquid": "Liquid",
    "semi_liquid": "Semi-liquid",
    "illiquid": "Illiquid",
}


def _type_label(value: str | None) -> str:
    if not value:
        return ""
    return _TYPE_LABELS.get(value, value)


def _account_row(account: dict) -> dict:
    return {
        "source": "account",
        "institution": account.get("institution") or "",
        "name": account.get("name") or "",
        "type_label": _type_label(account.get("type")),
        "tier": calculations.account_tier(account),
        "amount": calculations.account_contribution(account),
    }


def _asset_row(entry: dict) -> dict:
    return {
        "source": "debt" if entry.get("kind") == "debt" else "asset",
        "institution": entry.get("institution") or "",
        "name": entry.get("name") or "",
        "type_label": _type_label(entry.get("type")),
        "tier": calculations.asset_tier(entry),
        "amount": calculations.asset_contribution(entry),
    }


@holdings_bp.route("/<filename>/holdings")
def holdings_view(filename):
    snapshot_id = validate_snapshot(filename)
    edit_mode = request.args.get("edit") == "1"
    ctx = get_common_context(snapshot_id, filename, edit_mode)
    ctx["active_tab"] = "holdings"

    accounts = ctx["accounts"]
    assets = ctx["assets"]

    all_rows = [_account_row(a) for a in accounts] + [_asset_row(e) for e in assets]

    totals = calculations.tiered_totals(accounts, assets)
    equity = calculations.equity_pairs(assets)

    institutions = sorted({r["institution"] for r in all_rows if r["institution"]})

    tier_filter = request.args.get("tier") or ""
    if tier_filter not in LIQUIDITY_TIERS:
        tier_filter = ""
    liabilities_only = request.args.get("kind") == "liabilities"
    institution_filter = request.args.get("institution") or ""

    rows = all_rows
    if tier_filter:
        rows = [r for r in rows if r["tier"] == tier_filter]
    if liabilities_only:
        rows = [r for r in rows if r["amount"] < 0]
    if institution_filter:
        rows = [r for r in rows if r["institution"] == institution_filter]

    def _href(**overrides):
        params = {
            "tier": tier_filter,
            "kind": "liabilities" if liabilities_only else "",
            "institution": institution_filter,
        }
        params.update(overrides)
        params = {k: v for k, v in params.items() if v}
        if edit_mode:
            params["edit"] = "1"
        return url_for("holdings.holdings_view", filename=filename, **params)

    tier_chips = [
        {
            "value": tier,
            "label": TIER_LABELS[tier],
            "active": tier_filter == tier,
            "href": _href(tier="" if tier_filter == tier else tier),
        }
        for tier in LIQUIDITY_TIERS
    ]
    liabilities_chip = {
        "label": "Liabilities only",
        "active": liabilities_only,
        "href": _href(kind="" if liabilities_only else "liabilities"),
    }
    institution_chips = [
        {
            "value": inst,
            "label": inst,
            "active": institution_filter == inst,
            "href": _href(institution="" if institution_filter == inst else inst),
        }
        for inst in institutions
    ]

    has_filters = bool(tier_filter or liabilities_only or institution_filter)

    ctx["rows"] = rows
    ctx["totals"] = totals
    ctx["equity"] = equity
    ctx["tier_chips"] = tier_chips
    ctx["liabilities_chip"] = liabilities_chip
    ctx["institution_chips"] = institution_chips
    ctx["has_filters"] = has_filters
    ctx["clear_filters_href"] = url_for(
        "holdings.holdings_view", filename=filename, edit=1 if edit_mode else None
    )
    ctx["tier_labels"] = TIER_LABELS

    return render_template("holdings.html", **ctx)
