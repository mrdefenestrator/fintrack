"""Holdings blueprint - read-only unified view of accounts + assets/debts.

Lists every holding (account or asset_entries row) together with its liquidity
tier and signed net-worth contribution, reusing the shared spreadsheet chrome
(sheet table, filter bar, total row). Filtering only — editing stays on the
existing Accounts/Assets pages for now.
"""

from decimal import Decimal

from flask import Blueprint, render_template, request

from fintrack.core.formatting import fmt_money
from fintrack.core.types import HOLDING_TYPE_LABELS, HOLDING_TYPE_OPTIONS
from fintrack.networth import calculations

from .common import get_common_context, validate_snapshot

holdings_bp = Blueprint("holdings", __name__, url_prefix="/s")

# The one holding-type vocabulary drives the Type filter's options + labels.
_ALL_TYPE_OPTIONS: list[tuple[str, str]] = HOLDING_TYPE_OPTIONS
_TYPE_LABELS: dict[str, str] = HOLDING_TYPE_LABELS

# The two "balance side" filter buckets, keyed by the sign of a holding's
# contribution (assets add to net worth, liabilities subtract).
_BALANCE_LABELS: dict[str, str] = {"asset": "Assets", "liability": "Liabilities"}

# Column order for the holdings sheet. The money block reads qty × unit-price =
# amount. Unit and per-unit price are one combined "Unit Price" column showing
# the symbol and price together ("BTC $95,000"); USD rows leave it blank (the
# value is the Amount directly). Qty/Unit Price/Amount/Equity/LTV are right-
# aligned; Equity and LTV are last. Neither liquidity tier nor a Kind (account/
# asset/debt) column appears: both are derivable from type, so the Type column/
# filter already conveys them. The asset-vs-liability distinction is carried by
# the row accent + Balance filter.
_HEADERS = [
    "Institution",
    "Type",
    "Name",
    "Unit Price",
    "Qty",
    "Amount",
    "Equity",
    "LTV",
]
_RIGHT_ALIGN_COLS = [3, 4, 5, 6, 7]
_EQUITY_COL = 6
_LTV_COL = 7
_AMOUNT_COL = 5


def _type_label(value: str | None) -> str:
    if not value:
        return ""
    return _TYPE_LABELS.get(value, value)


def _fmt_qty(qty) -> str:
    """Plain quantity for the holdings sheet (coin/share counts).

    Unlike the shared fmt_qty (which renders fractions <1 as a percentage for
    fractional *ownership*), symbol quantities like 0.4015 BTC read as counts.
    Trailing zeros are stripped; whole numbers show without decimals.
    """
    if qty is None:
        return "—"
    d = Decimal(str(qty))
    if d == d.to_integral_value():
        return f"{int(d):,}"
    return format(d.normalize(), "f")


def _fmt_ltv(ltv: Decimal | None) -> str:
    return f"{ltv * 100:.1f}%" if ltv is not None else "—"


def _unit_price(unit: str, price) -> str:
    """Combined unit + per-unit price cell — only meaningful for symbol units.

    A symbol unit (BTC, AAPL, …) shows the ticker with its per-unit market
    price ("BTC $95,000"), or the bare ticker when no price is cached yet. USD
    rows have no per-unit price — the value is the Amount directly — so they
    show "—".
    """
    if not unit or unit == "USD":
        return "—"
    return f"{unit} {fmt_money(price)}" if price is not None else unit


def _holding(institution, name, type_value, unit, qty, price, amount):
    """Build one holding record: display cells + sort/filter metadata.

    The money block reads qty × unit-price = amount, where amount is the signed
    net-worth contribution. Accounts are the trivial USD case (qty/price blank,
    amount entered directly); assets show their quantity and per-unit price. The
    left-border accent encodes the asset/liability split by the sign of the
    amount (assets green, liabilities red).
    """
    is_liability = amount < 0
    return {
        "institution": institution,
        "name": name,
        "type_value": type_value,
        "balance_side": "liability" if is_liability else "asset",
        "amount": amount,
        "cells": [
            institution or "—",
            _type_label(type_value) or "—",
            name or "—",
            _unit_price(unit, price),
            _fmt_qty(qty),
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

    # Accounts are the trivial USD case: amount entered directly, no qty/price.
    rows = [
        _holding(
            a.get("institution") or "",
            a.get("name") or "",
            a.get("type"),
            "USD",
            None,
            None,
            calculations.account_contribution(a),
        )
        for a in accounts
    ]
    for e in assets:
        # Per-unit price is `value` for an asset, `balance` (owed per unit) for
        # a debt; amount is the signed subtotal (qty × price, liabilities −).
        price = e.get("value") if e.get("kind") == "asset" else e.get("balance")
        row = _holding(
            e.get("institution") or "",
            e.get("name") or "",
            e.get("type"),
            e.get("unit") or "USD",
            e.get("quantity"),
            price,
            calculations.asset_contribution(e),
        )
        pair = equity_by_debt.get(id(e))
        if pair is not None:
            row["cells"][_EQUITY_COL] = fmt_money(pair["equity"])
            row["cells"][_LTV_COL] = _fmt_ltv(pair["ltv"])
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
    total_cells = [""] * len(_HEADERS)
    total_cells[0] = "Total"
    total_cells[_AMOUNT_COL] = fmt_money(total)

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
