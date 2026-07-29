"""Holdings blueprint - unified view of accounts + assets/debts.

Renders ONE table split into four type-based domain groups:

    Cash · Credit Cards · Loans · Assets

Cash and Credit Cards are two type slices of the `accounts` table (credit cards
split out from spendable cash); Loans and Assets are the `kind=debt` / `kind=asset`
slices of the `asset_entries` table. So the split is purely a display grouping —
there is no data migration, and the debt↔asset equity/LTV pairing is untouched.

The leading columns — Institution · Type · Name · Amount — and the trailing
Due · Linked · As Of slots sit in the same slot positions in every group so they
align down the whole table; each group fills the middle slots with its own
columns (blank slots pad the shorter groups). Each group carries its own header
row and shows its subtotal in its heading band; a master footer closes the table
with the two totals that matter — Liquid and Net worth (`calculations.tiered_totals`).

Inline editing mirrors the Accounts/Assets pages (click a cell -> cell_edit
swaps the tbody with that cell in edit mode -> the input posts to update ->
tbody re-renders). Each row carries a `source` (account/asset) + `ref` so the
single update/delete routes dispatch to the right repository. Credit-card
Available is a computed, read-only column (Limit + owed balance), so it never
drifts from the imported balance. The actions column (delete) sticks to the
right edge.
"""

from datetime import date
from decimal import Decimal

from flask import Blueprint, abort, current_app, render_template, request

from fintrack.accounts.repository import (
    add_account,
    delete_account,
    get_accounts,
    reorder_accounts,
    update_account,
)
from fintrack.core.formatting import fmt_day_ordinal, fmt_money
from fintrack.core.types import (
    ACCOUNT_TYPE_OPTIONS,
    ASSET_TYPE_OPTIONS,
    HOLDING_TYPE_LABELS,
    HOLDING_TYPE_OPTIONS,
    HOLDING_TYPE_VALUES,
)
from fintrack.networth import calculations
from fintrack.networth.amortization import payoff_progress, scheduled_payment
from fintrack.networth.repository import (
    add_asset_entry,
    delete_asset_entry,
    get_asset_entries,
    reorder_asset_entries,
    update_asset_entry,
)

from .common import account_field_editable, get_common_context, validate_snapshot
from .crud import ACCOUNTS_COERCION, ASSETS_COERCION, coerce_value, handle_delete

holdings_bp = Blueprint("holdings", __name__, url_prefix="/s")


def _client_today() -> date | None:
    """The browser's local date, sent on every htmx request as X-Local-Date
    (base.html). Used to stamp a manual balance edit's As Of with the user's
    day rather than the server's timezone (QA #6). None if absent/malformed,
    in which case the repository falls back to the server's local date."""
    raw = request.headers.get("X-Local-Date")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


# The one holding-type vocabulary drives the Type filter's options + labels.
_ALL_TYPE_OPTIONS: list[tuple[str, str]] = HOLDING_TYPE_OPTIONS
_TYPE_LABELS: dict[str, str] = HOLDING_TYPE_LABELS

# The two "balance side" filter buckets, keyed by the sign of a holding's
# contribution (assets add to net worth, liabilities subtract).
_BALANCE_LABELS: dict[str, str] = {"asset": "Assets", "liability": "Liabilities"}

# ---------------------------------------------------------------------------
# Column layouts. Each tuple is (key, header, right_align, span).
#
# The table itself has one eight-column common spine:
# Institution · Type · Name · Amount · Details · Due · Linked · As Of.
# "Details" is one physical table cell containing a group-specific CSS grid.
# This keeps every genuinely shared column aligned without padding short groups
# out to the Loans group's column count.
# ---------------------------------------------------------------------------
_LEADING: list[tuple[str, str, bool, int]] = [
    ("institution", "Institution", False, 1),
    ("type", "Type", False, 1),
    ("name", "Name", False, 1),
    ("amount", "Amount", True, 1),
]
_DUE = ("due", "Due", False, 1)
_LINKED = ("linked", "Linked", False, 1)
_ASOF = ("as_of", "As Of", False, 1)
_DETAILS = ("details", "Details", False, 1)
_SPINE_COLS = _LEADING + [_DETAILS, _DUE, _LINKED, _ASOF]
_CASH_DETAILS = [
    ("reserve", "Reserve", True, 1),
    ("funding", "Funding", True, 1),
]
_CREDIT_DETAILS = [
    ("limit", "Limit", True, 1),
    ("available", "Available", True, 1),
    ("rewards", "Rewards", True, 1),
    ("statement", "Statement", True, 1),
]
_LOAN_DETAILS = [
    ("interest", "Interest", True, 1),
    ("equity", "Equity", True, 1),
    ("ltv", "LTV", True, 1),
    ("original", "Original", True, 1),
    ("term", "Term", True, 1),
    ("originated", "Originated", False, 1),
    ("payment", "P&I", True, 1),
    ("progress", "Paid", True, 1),
]
_ASSET_DETAILS = [
    ("unit_price", "Unit Price", True, 1),
    ("qty", "Qty", True, 1),
    ("source", "Source", False, 1),
]
_NCOLS = len(_SPINE_COLS)
_AMOUNT_POS = 3  # Amount is the 4th (leading) slot in every group
_DETAIL_POS = 4

# Group definitions: key -> (label, source, singular noun for the add button,
# detail layout). Order here is the top-to-bottom render order.
_GROUPS: list[tuple[str, str, str, str, list]] = [
    ("cash", "Cash", "account", "account", _CASH_DETAILS),
    ("credit", "Credit Cards", "account", "credit card", _CREDIT_DETAILS),
    ("loan", "Loans", "asset", "loan", _LOAN_DETAILS),
    ("asset", "Assets", "asset", "asset", _ASSET_DETAILS),
]
_GROUP_DETAILS = {key: cols for key, _, _, _, cols in _GROUPS}


def _account_group_key(a: dict) -> str:
    """Which group an account row belongs to (by type)."""
    t = a.get("type")
    if t == "credit_card":
        return "credit"
    if t == "loan":
        return "loan"
    return "cash"


def _asset_group_key(e: dict) -> str:
    """Which group an asset/debt entry belongs to (by kind)."""
    return "loan" if e.get("kind") == "debt" else "asset"


def _entity_group_key(source: str, entity: dict) -> str:
    return (
        _account_group_key(entity) if source == "account" else _asset_group_key(entity)
    )


def _col_keys(cols):
    return [k for k, _, _, _ in cols]


def _col_headers(cols):
    return [h for _, h, _, _ in cols]


def _col_right_align(cols):
    return [i for i, (_, _, ra, _) in enumerate(cols) if ra]


def _col_spans(cols):
    return [span for _, _, _, span in cols]


# Editable columns per account group, mapping the display column key to the
# underlying repository field. Available is deliberately absent: it is a
# computed, read-only column (Limit + owed balance), so it can never drift from
# the imported balance. Identity/classification fields are always editable; the
# money/detail fields are gated per row (below).
_CASH_COL_FIELD = [
    ("institution", "institution"),
    ("type", "type"),
    ("name", "name"),
    ("amount", "balance"),
    ("reserve", "minimum_balance"),
]
_CREDIT_COL_FIELD = [
    ("institution", "institution"),
    ("type", "type"),
    ("name", "name"),
    ("amount", "balance"),
    ("limit", "limit"),
    ("rewards", "rewards_balance"),
    ("statement", "statement_balance"),
    ("due", "statement_due_day_of_month"),
    ("linked", "paymentAccountRef"),
]
_ALWAYS_EDITABLE = ("institution", "type", "name")


def _account_col_fields(a: dict) -> dict:
    """Column key -> account field, gated by account_field_editable (CC vs cash,
    reserve types), so Holdings matches the Accounts page's editability. The map
    depends on the account's group (cash vs credit vs loan)."""
    key = _account_group_key(a)
    # Credit cards + account-loans edit the balance directly (Amount), since
    # Available is the computed column now (QA #10) — so `balance` is always
    # editable for them, overriding account_field_editable's Accounts-sheet rule
    # that treats a credit-card balance as derived/read-only.
    always = _ALWAYS_EDITABLE
    if key == "credit":
        col_field = _CREDIT_COL_FIELD
        always = _ALWAYS_EDITABLE + ("balance",)
    elif key == "loan":
        # A loan tracked as an account: Due + Linked activated (QA #9), amount
        # editable; no interest/equity/LTV (those are debt-entry concepts).
        col_field = [
            ("institution", "institution"),
            ("type", "type"),
            ("name", "name"),
            ("amount", "balance"),
            ("due", "statement_due_day_of_month"),
            ("linked", "paymentAccountRef"),
        ]
        always = _ALWAYS_EDITABLE + ("balance",)
    else:
        col_field = _CASH_COL_FIELD
    return {
        col_key: field
        for col_key, field in col_field
        if field in always or account_field_editable(a, field)
    }


def _asset_col_fields(e: dict) -> dict:
    """Column key -> asset/debt field. `value`/`balance` are editable through
    the Amount cell only when the row is a single USD unit (amount == the stored
    value); otherwise Amount is computed (edit qty, or the future price feed)."""
    is_debt = e.get("kind") == "debt"
    qty = e.get("quantity")
    single_unit = qty is None or qty == 1
    m = {
        "institution": "institution",
        "type": "type",
        "name": "name",
    }
    if is_debt:
        m["interest"] = "interestRate"
        m["due"] = "statement_due_day_of_month"
        m["linked"] = "assetRef"  # the asset this debt is secured by
        if e.get("type") == "loan":
            m["original"] = "originalPrincipal"
            m["term"] = "termMonths"
            m["originated"] = "originationDate"
        if single_unit:
            m["amount"] = "balance"
    else:
        m["unit_price"] = "unit"
        m["qty"] = "quantity"
        m["source"] = "source"
        if single_unit and (e.get("unit") or "USD") == "USD":
            m["amount"] = "value"
    return m


# As-of staleness thresholds (days) and colors, matching the accounts sparkline.
_STALE_AMBER_DAYS = 35
_STALE_RED_DAYS = 95

# Blank-cell marker (plain dash, as the Accounts/Assets tables use) and the
# muted color that de-emphasizes it so populated cells stand out.
_BLANK = "-"
_MUTED = "text-gray-300 dark:text-gray-600"


def _type_label(value: str | None) -> str:
    if not value:
        return ""
    return _TYPE_LABELS.get(value, value)


def _fmt_qty(qty) -> str:
    """Plain quantity for the holdings sheet (coin/share counts)."""
    if qty is None:
        return _BLANK
    d = Decimal(str(qty))
    if d == d.to_integral_value():
        return f"{int(d):,}"
    return format(d.normalize(), "f")


def _fmt_ltv(ltv: Decimal | None) -> str:
    return f"{ltv * 100:.1f}%" if ltv is not None else _BLANK


def _fmt_pct(rate) -> str:
    return f"{rate * 100:.2f}%" if rate is not None else _BLANK


def _money(x) -> str:
    return fmt_money(x) if x is not None else _BLANK


def _unit_price(unit: str, price) -> str:
    """Combined unit + per-unit price cell — only meaningful for symbol units."""
    if not unit or unit == "USD":
        return _BLANK
    return f"{unit} {fmt_money(price)}" if price is not None else unit


def _cc_available(a: dict) -> str:
    """Computed, read-only credit available = credit limit + owed balance (QA
    #10). Blank when no limit is set. Rewards don't affect the credit line, so
    this uses the signed owed balance, not the net Amount."""
    limit = a.get("limit")
    if limit is None:
        return _BLANK
    owed = calculations._credit_card_balance_owed(a)  # signed (negative = owed)
    return fmt_money(_money_dec(limit) + owed)


def _money_dec(x) -> Decimal:
    return Decimal(str(x)) if x is not None else Decimal("0")


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


def _raw(v) -> str:
    """Raw scalar as a string for prefilling an edit input (None -> '')."""
    return "" if v is None else str(v)


def _make_row(
    values,
    amount,
    type_value,
    institution,
    today,
    source,
    ref,
    detail_cols,
    col_fields,
    edit_raw,
    liability,
):
    """Assemble a row record: display cells, per-cell classes, edit metadata.

    The physical cells follow `_SPINE_COLS`; the middle Details cell contains
    the group's own `detail_cols`. The left accent encodes the asset/
    liability split by the holding's *group* — Credit Cards and Loans are
    liabilities (red) even at a zero balance; Cash and Assets are green — rather
    than by the sign of the current amount.
    """
    keys = _col_keys(_SPINE_COLS)
    detail_keys = _col_keys(detail_cols)
    is_liability = liability
    cells = ["" if k == "details" else values.get(k, _BLANK) for k in keys]
    cell_classes = [
        "" if (k == "details" or cells[i] != _BLANK) else _MUTED
        for i, k in enumerate(keys)
    ]
    if "as_of" in keys:
        staleness = _staleness_class(values.get("as_of_iso"), today)
        if staleness:
            cell_classes[keys.index("as_of")] = staleness
    return {
        "source": source,
        "ref": ref,
        "institution": institution,
        "type_value": type_value,
        "balance_side": "liability" if is_liability else "asset",
        "amount": amount,
        "cells": cells,
        "cell_classes": cell_classes,
        # Blank slots carry no field (never editable).
        "fields": [None if k == "" else col_fields.get(k) for k in keys],
        "detail_cells": [values.get(k, _BLANK) for k in detail_keys],
        "detail_fields": [col_fields.get(k) for k in detail_keys],
        "detail_classes": [
            "" if values.get(k, _BLANK) != _BLANK else _MUTED for k in detail_keys
        ],
        "edit_raw": edit_raw,
        "accent_side": "liability" if is_liability else "asset",
    }


def _account_row(a: dict, funding_by_id: dict, account_display: dict, today: date):
    amount = calculations.account_contribution(a)
    due_day = a.get("statement_due_day_of_month")
    pay_ref = a.get("paymentAccountRef")
    funding = funding_by_id.get(a.get("id"))
    group_key = _account_group_key(a)
    values = {
        "institution": a.get("institution") or _BLANK,
        "type": _type_label(a.get("type")) or _BLANK,
        "name": a.get("name") or _BLANK,
        "amount": fmt_money(amount),
        "rewards": _money(a.get("rewards_balance")),
        "limit": _money(a.get("limit")),
        "available": _cc_available(a),  # computed, read-only (QA #10)
        "statement": _money(a.get("statement_balance")),
        "due": fmt_day_ordinal(due_day) if due_day else _BLANK,
        # Linked holding: for a credit card, the account that pays it.
        "linked": account_display.get(pay_ref, _BLANK) if pay_ref else _BLANK,
        "reserve": _money(a.get("minimum_balance")),
        "funding": fmt_money(funding) if funding else _BLANK,  # None/0 -> blank
        "as_of": a.get("asOfDate") or _BLANK,
        "as_of_iso": a.get("asOfDate"),
    }
    edit_raw = {
        "institution": a.get("institution") or "",
        "type": a.get("type") or "",
        "name": a.get("name") or "",
        "balance": _raw(a.get("balance")),
        "rewards_balance": _raw(a.get("rewards_balance")),
        "limit": _raw(a.get("limit")),
        "statement_balance": _raw(a.get("statement_balance")),
        "statement_due_day_of_month": _raw(a.get("statement_due_day_of_month")),
        "paymentAccountRef": _raw(a.get("paymentAccountRef")),
        "minimum_balance": _raw(a.get("minimum_balance")),
    }
    return group_key, _make_row(
        values,
        amount,
        a.get("type"),
        a.get("institution") or "",
        today,
        "account",
        a.get("id"),
        _GROUP_DETAILS[group_key],
        _account_col_fields(a),
        edit_raw,
        liability=group_key in ("credit", "loan"),
    )


def _asset_row(e: dict, pair: dict | None, linked: str, index: int, today: date):
    is_debt = e.get("kind") == "debt"
    price = e.get("balance") if is_debt else e.get("value")
    amount = calculations.asset_contribution(e)
    group_key = _asset_group_key(e)
    has_amortization = is_debt and e.get("type") == "loan"
    payment = (
        scheduled_payment(
            e.get("originalPrincipal"), e.get("interestRate"), e.get("termMonths")
        )
        if has_amortization
        else None
    )
    progress = (
        payoff_progress(e.get("originalPrincipal"), e.get("balance"))
        if has_amortization
        else None
    )
    due_day = e.get("statement_due_day_of_month")
    values = {
        "institution": e.get("institution") or _BLANK,
        "type": _type_label(e.get("type")) or _BLANK,
        "name": e.get("name") or _BLANK,
        "unit_price": _unit_price(e.get("unit") or "USD", price),
        "qty": _fmt_qty(e.get("quantity")),
        "amount": fmt_money(amount),
        "due": fmt_day_ordinal(due_day) if is_debt and due_day else _BLANK,
        # Linked: for a loan, the asset it is secured by; for an asset, the
        # loan(s) secured against it.
        "linked": linked,
        "interest": _fmt_pct(e.get("interestRate")),
        "original": (
            _money(e.get("originalPrincipal")) if has_amortization else _BLANK
        ),
        "term": (
            f"{e['termMonths']} mo"
            if has_amortization and e.get("termMonths")
            else _BLANK
        ),
        "originated": (
            e.get("originationDate") or _BLANK if has_amortization else _BLANK
        ),
        "payment": _money(payment),
        "progress": _fmt_ltv(progress),
        "source": _BLANK if is_debt else (e.get("source") or _BLANK),
        "as_of": e.get("asOfDate") or _BLANK,
        "as_of_iso": e.get("asOfDate"),
    }
    if pair is not None:
        values["equity"] = fmt_money(pair["equity"])
        values["ltv"] = _fmt_ltv(pair["ltv"])
    edit_raw = {
        "institution": e.get("institution") or "",
        "type": e.get("type") or "",
        "name": e.get("name") or "",
        "unit": e.get("unit") or "USD",
        "quantity": _raw(e.get("quantity")),
        "value": _raw(e.get("value")),
        "balance": _raw(e.get("balance")),
        "interestRate": _raw(e.get("interestRate")),
        "originalPrincipal": _raw(e.get("originalPrincipal")),
        "termMonths": _raw(e.get("termMonths")),
        "originationDate": e.get("originationDate") or "",
        "statement_due_day_of_month": _raw(e.get("statement_due_day_of_month")),
        "assetRef": _raw(e.get("assetRef")),
        "source": e.get("source") or "",
    }
    return group_key, _make_row(
        values,
        amount,
        e.get("type"),
        e.get("institution") or "",
        today,
        "asset",
        index,
        _GROUP_DETAILS[group_key],
        _asset_col_fields(e),
        edit_raw,
        liability=group_key == "loan",
    )


def _all_rows(ctx: dict, today: date) -> dict[str, list[dict]]:
    """Build every holding row, bucketed by group key."""
    accounts = ctx["accounts"]
    assets = ctx["assets"]
    budget = ctx["budget"]
    account_display = ctx["account_display_by_id"]

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

    # Asset ↔ loan links (via asset_ref) for the "Linked" column, both ways.
    asset_name_by_id = {
        e["id"]: e.get("name") or _BLANK
        for e in assets
        if e.get("kind") == "asset" and e.get("id") is not None
    }
    loans_by_asset_id: dict[int, list[str]] = {}
    for e in assets:
        ref = e.get("assetRef")
        if e.get("kind") == "debt" and ref is not None:
            loans_by_asset_id.setdefault(ref, []).append(e.get("name") or _BLANK)

    def _linked(e: dict) -> str:
        if e.get("kind") == "debt":
            ref = e.get("assetRef")
            return asset_name_by_id.get(ref, _BLANK) if ref is not None else _BLANK
        names = loans_by_asset_id.get(e.get("id"), [])
        return ", ".join(names) if names else _BLANK

    rows: dict[str, list[dict]] = {key: [] for key, *_ in _GROUPS}
    for a in accounts:
        key, row = _account_row(a, funding_by_id, account_display, today)
        rows[key].append(row)
    for idx, e in enumerate(assets):
        key, row = _asset_row(e, equity_by_debt.get(id(e)), _linked(e), idx, today)
        rows[key].append(row)
    return rows


def _apply_filters(rows, type_sel, balance_sel, inst_sel):
    if type_sel:
        rows = [r for r in rows if r["type_value"] in type_sel]
    if balance_sel:
        rows = [r for r in rows if r["balance_side"] in balance_sel]
    if inst_sel:
        rows = [r for r in rows if r["institution"] in inst_sel]
    return rows


def _groups_ctx(rows_by_group: dict[str, list[dict]], accounts, assets):
    """The four domain groups (headers, rows, group total, reorder wiring) + the
    master footer (Liquid + Net worth).

    A group's rows can only be drag-reordered when they all share one source
    table (the normal case); a mixed group (e.g. an account-loan sitting beside
    debt-entry loans) is left non-reorderable rather than trying to permute two
    tables at once.
    """
    groups = []
    for key, label, source, add_noun, detail_cols in _GROUPS:
        grp_rows = rows_by_group.get(key, [])
        total = sum((r["amount"] for r in grp_rows), Decimal("0"))
        sources = {r["source"] for r in grp_rows}
        reorderable = len(sources) == 1
        groups.append(
            {
                "key": key,
                "label": label,
                "source": source,
                "add_noun": add_noun,
                "headers": _col_headers(_SPINE_COLS),
                "right_align": _col_right_align(_SPINE_COLS),
                "spans": _col_spans(_SPINE_COLS),
                "detail_headers": _col_headers(detail_cols),
                "detail_keys": _col_keys(detail_cols),
                "detail_right_align": _col_right_align(detail_cols),
                "rows": grp_rows,
                "total": fmt_money(total),
                "reorderable": reorderable,
            }
        )

    totals = calculations.tiered_totals(accounts, assets)
    master = []
    for label, value in (
        ("Liquid", totals["liquid"]),
        ("Net worth", totals["net_worth"]),
    ):
        cells = [""] * _NCOLS
        cells[2] = label
        cells[_AMOUNT_POS] = fmt_money(value)
        master.append(cells)
    return groups, master


def _ref_options(ctx: dict) -> tuple[list, list]:
    """(account, asset) picker options for the Linked column: a credit card's
    payment account, and a loan's secured asset."""
    account_display = ctx["account_display_by_id"]
    account_ref_options = [
        (a["id"], account_display.get(a["id"], a.get("name") or _BLANK))
        for a in ctx["accounts"]
        if a.get("id") is not None
    ]
    asset_ref_options = [
        (e["id"], e.get("name") or _BLANK)
        for e in ctx["assets"]
        if e.get("kind") == "asset" and e.get("id") is not None
    ]
    return account_ref_options, asset_ref_options


def _tbody_ctx(rows_by_group, ctx: dict, filters_active=False, **editing) -> dict:
    """Shared context for the tbody partial (page render and edit swaps)."""
    account_ref_options, asset_ref_options = _ref_options(ctx)
    groups, master_total = _groups_ctx(rows_by_group, ctx["accounts"], ctx["assets"])
    return {
        "groups": groups,
        "master_total": master_total,
        "filters_active": filters_active,
        "ncols": _NCOLS,
        "amount_pos": _AMOUNT_POS,
        "detail_pos": _DETAIL_POS,
        "account_type_options": ACCOUNT_TYPE_OPTIONS,
        "asset_type_options": ASSET_TYPE_OPTIONS,
        "account_ref_options": account_ref_options,
        "asset_ref_options": asset_ref_options,
        **editing,
    }


def _render_tbody(snapshot_id, filename, error=None, **editing):
    """Render just the tbody (for cell_edit / update HTMX swaps), edit mode on."""
    ctx = get_common_context(snapshot_id, filename, edit_mode=True)
    rows_by_group = _all_rows(ctx, date.today())
    tbody = _tbody_ctx(
        rows_by_group,
        ctx,
        edit_mode=True,
        filename=filename,
        error=error,
        **editing,
    )
    return render_template("partials/holdings_tbody.html", **tbody)


@holdings_bp.route("/<filename>/holdings")
def holdings_view(filename):
    snapshot_id = validate_snapshot(filename)
    edit_mode = request.args.get("edit") == "1"
    ctx = get_common_context(snapshot_id, filename, edit_mode)
    ctx["active_tab"] = "holdings"

    rows_by_group = _all_rows(ctx, date.today())
    all_rows = [r for rows in rows_by_group.values() for r in rows]
    institutions = sorted({r["institution"] for r in all_rows if r["institution"]})
    present_types = {r["type_value"] for r in all_rows if r["type_value"]}
    type_values_labels = [
        (v, lbl) for v, lbl in _ALL_TYPE_OPTIONS if v in present_types
    ]

    # --- filters (multi-select, form-submit; mirrors the Assets sheet) ---
    type_sel = [t for t in request.args.getlist("type") if t in present_types]
    balance_sel = [b for b in request.args.getlist("balance") if b in _BALANCE_LABELS]
    inst_sel = [i for i in request.args.getlist("institution") if i in institutions]

    filtered = {
        key: _apply_filters(rows, type_sel, balance_sel, inst_sel)
        for key, rows in rows_by_group.items()
    }

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

    ctx.update(
        _tbody_ctx(
            filtered,
            ctx,
            filters_active=bool(type_sel or balance_sel or inst_sel),
            edit_mode=edit_mode,
        )
    )
    ctx.update(
        {
            "active_count": active_count,
            "type_opts": _opts(type_values_labels, type_sel),
            "balance_opts": _opts(list(_BALANCE_LABELS.items()), balance_sel),
            "institution_opts": _opts([(i, i) for i in institutions], inst_sel),
        }
    )
    return render_template("holdings.html", **ctx)


def _load_entity(snapshot_id: int, source: str, ref: int):
    """The account (by id) or asset entry (by sort-order index) for a row."""
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        if source == "account":
            return next(
                (a for a in get_accounts(conn, snapshot_id) if a.get("id") == ref),
                None,
            )
        entries = get_asset_entries(conn, snapshot_id)
        return entries[ref] if 0 <= ref < len(entries) else None


def _field_editable(source: str, entity: dict | None, field: str) -> bool:
    """Whether `field` is editable for this specific row (same gating as the
    per-row column map that drives the sheet's edit cells)."""
    if entity is None:
        return False
    col_fields = _account_col_fields if source == "account" else _asset_col_fields
    return field in col_fields(entity).values()


@holdings_bp.route("/<filename>/holdings/cell/<source>/<int:ref>")
def cell_edit(filename: str, source: str, ref: int):
    """Return the tbody with one cell in edit mode (or reverted on display=1)."""
    snapshot_id = validate_snapshot(filename)
    if source not in ("account", "asset"):
        abort(404)
    if request.args.get("display") == "1":
        return _render_tbody(snapshot_id, filename)
    field = request.args.get("field", "")
    if not _field_editable(source, _load_entity(snapshot_id, source, ref), field):
        return _render_tbody(snapshot_id, filename)
    return _render_tbody(
        snapshot_id,
        filename,
        editing_source=source,
        editing_ref=ref,
        editing_field=field,
    )


def _coerce(source: str, field: str, value_raw: str) -> tuple:
    """Validate/normalize an edit value. Returns (value, error)."""
    if field == "type":
        if value_raw == "":
            return None, None  # clear -> unclassified
        if value_raw not in HOLDING_TYPE_VALUES:
            return None, "Invalid type"
        return value_raw, None
    if field == "name":
        if not value_raw:
            return None, "Name is required"
        return value_raw, None
    if field == "unit":
        return (value_raw.upper() or "USD"), None
    if field in ("institution", "source", "originationDate"):
        return (value_raw or None), None
    cmap = ACCOUNTS_COERCION if source == "account" else ASSETS_COERCION
    value, error = coerce_value(field, value_raw, cmap)
    if error:
        return value, error
    if field == "statement_due_day_of_month" and value is not None:
        if not 1 <= value <= 31:
            return None, "Due day must be between 1 and 31"
    if field == "termMonths" and value is not None and value <= 0:
        return None, "Term must be greater than zero"
    if field == "originalPrincipal" and value is not None and value <= 0:
        return None, "Original principal must be greater than zero"
    return value, None


@holdings_bp.route("/<filename>/holdings/update/<source>/<int:ref>", methods=["POST"])
def update(filename: str, source: str, ref: int):
    snapshot_id = validate_snapshot(filename)
    if source not in ("account", "asset"):
        abort(404)
    field = request.form.get("field", "").strip()
    value_raw = request.form.get("value", "").strip()
    if not _field_editable(source, _load_entity(snapshot_id, source, ref), field):
        return _render_tbody(snapshot_id, filename), 422

    value, error = _coerce(source, field, value_raw)
    if error:
        return _render_tbody(snapshot_id, filename, error=error), 422

    engine = current_app.config["engine"]
    try:
        with engine.connect() as conn:
            if source == "account":
                update_account(
                    conn, snapshot_id, ref, {field: value}, today=_client_today()
                )
            else:
                update_asset_entry(conn, snapshot_id, ref, {field: value})
    except ValueError:
        return _render_tbody(snapshot_id, filename, error="Could not save"), 422

    return _render_tbody(
        snapshot_id,
        filename,
        updated_source=source,
        updated_ref=ref,
        updated_field=field,
    )


@holdings_bp.route("/<filename>/holdings/delete-btn/<source>/<int:ref>")
def delete_btn(filename: str, source: str, ref: int):
    """Restore the actions cell (cancel of a delete confirm)."""
    validate_snapshot(filename)
    if source not in ("account", "asset"):
        abort(404)
    return render_template(
        "partials/holdings_actions_cell.html",
        filename=filename,
        source=source,
        ref=ref,
        edit_mode=True,
    )


@holdings_bp.route("/<filename>/holdings/delete-confirm/<source>/<int:ref>")
def delete_confirm(filename: str, source: str, ref: int):
    """Swap the actions cell for a Yes/No delete confirmation."""
    validate_snapshot(filename)
    if source not in ("account", "asset"):
        abort(404)
    return render_template(
        "partials/holdings_delete_confirm.html",
        filename=filename,
        source=source,
        ref=ref,
    )


@holdings_bp.route("/<filename>/holdings/delete/<source>/<int:ref>", methods=["POST"])
def delete(filename: str, source: str, ref: int):
    snapshot_id = validate_snapshot(filename)
    if source not in ("account", "asset"):
        abort(404)
    engine = current_app.config["engine"]

    def _do(conn):
        if source == "account":
            delete_account(conn, snapshot_id, ref)
        else:
            delete_asset_entry(conn, snapshot_id, ref)

    return handle_delete(_do, engine)


@holdings_bp.route("/<filename>/holdings/reorder/<group>", methods=["POST"])
def reorder(filename: str, group: str):
    """Persist a within-group drag reorder.

    The posted `order` is a permutation of the group's own 0-based row positions
    (local to the group). Because two groups can share one table (Cash/Credit in
    `accounts`; Loans/Assets in `asset_entries`), the local permutation is mapped
    onto the group's *global* slots in that table, leaving the other group's rows
    fixed, then persisted as a full table permutation.
    """
    snapshot_id = validate_snapshot(filename)
    if group not in _GROUP_DETAILS:
        abort(404)
    source = "account" if group in ("cash", "credit") else "asset"
    try:
        local_order = [
            int(x) for x in request.form.get("order", "").split(",") if x != ""
        ]
    except ValueError:
        return "", 400

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        entities = (
            get_accounts(conn, snapshot_id)
            if source == "account"
            else get_asset_entries(conn, snapshot_id)
        )
        positions = [
            i
            for i, ent in enumerate(entities)
            if _entity_group_key(source, ent) == group
        ]
        if sorted(local_order) != list(range(len(positions))):
            return "", 400
        # Map the group's local permutation onto its global slots; other rows
        # keep their positions.
        full = list(range(len(entities)))
        for new_local, old_local in enumerate(local_order):
            full[positions[new_local]] = positions[old_local]
        try:
            if source == "account":
                reorder_accounts(conn, snapshot_id, full)
            else:
                reorder_asset_entries(conn, snapshot_id, full)
        except ValueError:
            return "", 400
    return "", 204


@holdings_bp.route("/<filename>/holdings/add/<group>", methods=["POST"])
def add(filename: str, group: str):
    """Add a blank holding to a group; the user then edits it inline."""
    snapshot_id = validate_snapshot(filename)
    if group not in _GROUP_DETAILS:
        abort(404)
    engine = current_app.config["engine"]
    today = _client_today()
    with engine.connect() as conn:
        if group == "cash":
            add_account(
                conn,
                snapshot_id,
                {"name": "New account", "type": "checking"},
                today=today,
            )
        elif group == "credit":
            add_account(
                conn,
                snapshot_id,
                {"name": "New card", "type": "credit_card"},
                today=today,
            )
        elif group == "loan":
            add_asset_entry(
                conn, snapshot_id, {"kind": "debt", "type": "loan", "name": "New loan"}
            )
        else:
            add_asset_entry(conn, snapshot_id, {"kind": "asset", "name": "New asset"})
    resp = current_app.make_response("")
    resp.headers["HX-Refresh"] = "true"
    return resp
