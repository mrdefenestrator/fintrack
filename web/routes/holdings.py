"""Holdings blueprint - read-only unified view of accounts + assets/debts.

Lists every holding (account or asset_entries row) together with its liquidity
tier and signed net-worth contribution, reusing the shared spreadsheet chrome
(sheet table, filter bar, total row). One dense sheet that combines the columns
of the Accounts and Assets pages; type-specific cells are blank ("—") on rows
they don't apply to. Filtering only — editing stays on the existing Accounts/
Assets pages for now.
"""

from datetime import date
from decimal import Decimal

from flask import Blueprint, render_template, request

from fintrack.core.formatting import fmt_day_ordinal, fmt_money
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

# The full column set for the combined sheet, as (key, header, right_align).
# It unions the Accounts and Assets columns; each row fills the cells that apply
# to it and leaves the rest blank. The money block reads qty × unit-price =
# amount (Unit Price is the combined symbol+price cell; USD rows leave it
# blank). Neither liquidity tier nor a Kind (account/asset/debt) column appears:
# both are derivable from type. Asset-vs-liability is carried by the row accent.
_COLUMNS: list[tuple[str, str, bool]] = [
    ("institution", "Institution", False),
    ("type", "Type", False),
    ("name", "Name", False),
    ("unit_price", "Unit Price", True),
    ("qty", "Qty", True),
    ("amount", "Amount", True),
    ("rewards", "Rewards", True),
    ("limit", "Limit", True),
    ("available", "Available", True),
    ("statement", "Statement", True),
    ("due", "Due", False),
    ("payment", "Payment Acct", False),
    ("interest", "Interest", True),
    ("reserve", "Reserve", True),
    ("funding", "Funding", True),
    ("source", "Source", False),
    ("as_of", "As Of", False),
    ("equity", "Equity", True),
    ("ltv", "LTV", True),
]
_HEADERS = [h for _, h, _ in _COLUMNS]
_RIGHT_ALIGN_COLS = [i for i, (_, _, ra) in enumerate(_COLUMNS) if ra]
_KEYS = [k for k, _, _ in _COLUMNS]
_AMOUNT_COL = _KEYS.index("amount")
_AS_OF_COL = _KEYS.index("as_of")

# As-of staleness thresholds (days) and colors, matching the accounts sparkline.
_STALE_AMBER_DAYS = 35
_STALE_RED_DAYS = 95


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


def _fmt_pct(rate) -> str:
    return f"{rate * 100:.2f}%" if rate is not None else "—"


def _money(x) -> str:
    return fmt_money(x) if x is not None else "—"


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


def _staleness_class(as_of_iso: str | None, today: date) -> str:
    """Color the As-of cell by age: amber past 35 days, red past 95, else none."""
    if not as_of_iso:
        return ""
    try:
        age = (today - date.fromisoformat(as_of_iso)).days
    except ValueError:
        return ""
    if age > _STALE_RED_DAYS:
        return "text-red-600 dark:text-red-400"
    if age > _STALE_AMBER_DAYS:
        return "text-amber-600 dark:text-amber-400"
    return ""


def _make_row(values: dict, amount: Decimal, type_value, institution, today: date):
    """Assemble a row record: display cells + per-cell classes + filter meta.

    The left-border accent encodes the asset/liability split by the sign of the
    amount (assets green, liabilities red).
    """
    is_liability = amount < 0
    cells = [values.get(k, "—") for k in _KEYS]
    cell_classes = [""] * len(_KEYS)
    cell_classes[_AS_OF_COL] = _staleness_class(values.get("as_of_iso"), today)
    return {
        "institution": institution,
        "type_value": type_value,
        "balance_side": "liability" if is_liability else "asset",
        "amount": amount,
        "cells": cells,
        "cell_classes": cell_classes,
        "accent": "border-l-rose-400" if is_liability else "border-l-emerald-400",
    }


def _account_row(a: dict, funding_by_id: dict, account_display: dict, today: date):
    amount = calculations.account_contribution(a)
    due_day = a.get("statement_due_day_of_month")
    pay_ref = a.get("paymentAccountRef")
    funding = funding_by_id.get(a.get("id"))
    values = {
        "institution": a.get("institution") or "—",
        "type": _type_label(a.get("type")) or "—",
        "name": a.get("name") or "—",
        "amount": fmt_money(amount),
        "rewards": _money(a.get("rewards_balance")),
        "limit": _money(a.get("limit")),
        "available": _money(a.get("available")),
        "statement": _money(a.get("statement_balance")),
        "due": fmt_day_ordinal(due_day) if due_day else "—",
        "payment": account_display.get(pay_ref, "—") if pay_ref else "—",
        "reserve": _money(a.get("minimum_balance")),
        "funding": fmt_money(funding) if funding else "—",  # None/0 -> "—"
        "as_of": a.get("asOfDate") or "—",
        "as_of_iso": a.get("asOfDate"),
    }
    return _make_row(values, amount, a.get("type"), a.get("institution") or "", today)


def _asset_row(e: dict, pair: dict | None, today: date):
    is_debt = e.get("kind") == "debt"
    # Per-unit price is `value` for an asset, `balance` (owed per unit) for a
    # debt; amount is the signed subtotal (qty × price, liabilities −).
    price = e.get("balance") if is_debt else e.get("value")
    amount = calculations.asset_contribution(e)
    values = {
        "institution": e.get("institution") or "—",
        "type": _type_label(e.get("type")) or "—",
        "name": e.get("name") or "—",
        "unit_price": _unit_price(e.get("unit") or "USD", price),
        "qty": _fmt_qty(e.get("quantity")),
        "amount": fmt_money(amount),
        "due": e.get("nextDueDate") if is_debt and e.get("nextDueDate") else "—",
        "interest": _fmt_pct(e.get("interestRate")),
        "source": "—" if is_debt else (e.get("source") or "—"),
        "as_of": e.get("asOfDate") or "—",
        "as_of_iso": e.get("asOfDate"),
    }
    if pair is not None:
        values["equity"] = fmt_money(pair["equity"])
        values["ltv"] = _fmt_ltv(pair["ltv"])
    return _make_row(values, amount, e.get("type"), e.get("institution") or "", today)


@holdings_bp.route("/<filename>/holdings")
def holdings_view(filename):
    snapshot_id = validate_snapshot(filename)
    edit_mode = request.args.get("edit") == "1"
    ctx = get_common_context(snapshot_id, filename, edit_mode)
    ctx["active_tab"] = "holdings"

    accounts = ctx["accounts"]
    assets = ctx["assets"]
    budget = ctx["budget"]
    account_display = ctx["account_display_by_id"]
    today = date.today()

    # Funding needed for cash (liquid) accounts only, matching the Accounts page.
    funding_by_id = {
        a["id"]: calculations.account_funding_needed(
            a, accounts, budget, today, default_reserve=0
        )["funding_needed"]
        for a in accounts
        if calculations._ACCOUNT_TYPE_TO_CALCULATION.get(a.get("type")) == "liquid"
    }

    # Equity/LTV for secured debts, keyed by the debt entry's identity so we can
    # fold it into that loan's row (per design: equity shows on the loan row).
    equity_by_debt = {id(p["debt"]): p for p in calculations.equity_pairs(assets)}

    rows = [_account_row(a, funding_by_id, account_display, today) for a in accounts]
    rows += [_asset_row(e, equity_by_debt.get(id(e)), today) for e in assets]

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
