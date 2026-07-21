"""Holdings blueprint - unified view of accounts + assets/debts.

Renders one table with two domain groups (Accounts, Assets). The leading columns
— Institution · Type · Name · Amount — are identical in both groups so they align
down the whole table; the trailing column slots are reused per group (e.g. slot 4
is Rewards for accounts, Unit Price for assets), so each group carries its own
header row and the table stays narrow. Each group has a subtotal; a master
Net-worth total closes the table.

Inline editing mirrors the Accounts/Assets pages (click a cell -> cell_edit
swaps the tbody with that cell in edit mode -> the input posts to update ->
tbody re-renders). Because a Holdings row is either an account or an asset entry,
each row carries a `source` + `ref` so the single update/delete routes dispatch
to the right repository. Editable fields, per-row gating, and coercion match the
Accounts/Assets pages. The actions column (delete) sticks to the right edge.
"""

from datetime import date
from decimal import Decimal

from flask import Blueprint, abort, current_app, render_template, request

from fintrack.accounts.repository import (
    delete_account,
    get_accounts,
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
from fintrack.networth.repository import (
    delete_asset_entry,
    get_asset_entries,
    update_asset_entry,
)

from .common import account_field_editable, get_common_context, validate_snapshot
from .crud import ACCOUNTS_COERCION, ASSETS_COERCION, coerce_value, handle_delete

holdings_bp = Blueprint("holdings", __name__, url_prefix="/s")

# The one holding-type vocabulary drives the Type filter's options + labels.
_ALL_TYPE_OPTIONS: list[tuple[str, str]] = HOLDING_TYPE_OPTIONS
_TYPE_LABELS: dict[str, str] = HOLDING_TYPE_LABELS

# The two "balance side" filter buckets, keyed by the sign of a holding's
# contribution (assets add to net worth, liabilities subtract).
_BALANCE_LABELS: dict[str, str] = {"asset": "Assets", "liability": "Liabilities"}

# Holdings renders as ONE table with two domain groups (Accounts, Assets). The
# leading columns — Institution · Type · Name · Amount — are identical in both
# groups so they align down the whole table (auto width sized across both
# domains). The trailing column *slots* are reused: e.g. slot 4 is Rewards for
# accounts and Unit Price for assets. Each group carries its own header row, so
# the table stays narrow (4 + max trailing) instead of the union width. Both
# groups have the same slot count so the grid lines up.
_ACCOUNT_COLS: list[tuple[str, str, bool]] = [
    ("institution", "Institution", False),
    ("type", "Type", False),
    ("name", "Name", False),
    ("amount", "Amount", True),
    ("rewards", "Rewards", True),
    ("limit", "Limit", True),
    ("available", "Available", True),
    ("statement", "Statement", True),
    ("due", "Due", False),
    ("linked", "Linked", False),
    ("reserve", "Reserve", True),
    ("funding", "Funding", True),
    ("as_of", "As Of", False),
]
_ASSET_COLS: list[tuple[str, str, bool]] = [
    ("institution", "Institution", False),
    ("type", "Type", False),
    ("name", "Name", False),
    ("amount", "Amount", True),
    ("unit_price", "Unit Price", True),
    ("qty", "Qty", True),
    ("interest", "Interest", True),
    ("due", "Due", False),
    ("linked", "Linked", False),
    ("source", "Source", False),
    ("as_of", "As Of", False),
    ("equity", "Equity", True),
    ("ltv", "LTV", True),
]
_NCOLS = len(_ACCOUNT_COLS)  # same slot count in both groups
_AMOUNT_POS = 3  # Amount is the 4th (leading) slot in both groups


def _col_keys(cols):
    return [k for k, _, _ in cols]


def _col_headers(cols):
    return [h for _, h, _ in cols]


def _col_right_align(cols):
    return [i for i, (_, _, ra) in enumerate(cols) if ra]


# Editable columns per row source, mapping the display column key to the
# underlying repository field. Identity/classification fields are always
# editable; the money/detail fields are gated per row (below), so the sheet's
# edit behavior matches the Accounts/Assets pages.
_ACCOUNT_COL_FIELD = [
    ("institution", "institution"),
    ("type", "type"),
    ("name", "name"),
    ("amount", "balance"),
    ("rewards", "rewards_balance"),
    ("limit", "limit"),
    ("available", "available"),
    ("statement", "statement_balance"),
    ("due", "statement_due_day_of_month"),
    ("linked", "paymentAccountRef"),
    ("reserve", "minimum_balance"),
]
_ALWAYS_EDITABLE = ("institution", "type", "name")


def _account_col_fields(a: dict) -> dict:
    """Column key -> account field, gated by account_field_editable (CC vs cash,
    reserve types), so Holdings matches the Accounts page's editability."""
    return {
        key: field
        for key, field in _ACCOUNT_COL_FIELD
        if field in _ALWAYS_EDITABLE or account_field_editable(a, field)
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
        "qty": "quantity",
    }
    if is_debt:
        m["interest"] = "interestRate"
        m["due"] = "nextDueDate"
        m["linked"] = "assetRef"  # the asset this debt is secured by
        if single_unit:
            m["amount"] = "balance"
    else:
        m["unit_price"] = "unit"
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
    """Plain quantity for the holdings sheet (coin/share counts).

    Unlike the shared fmt_qty (which renders fractions <1 as a percentage for
    fractional *ownership*), symbol quantities like 0.4015 BTC read as counts.
    Trailing zeros are stripped; whole numbers show without decimals.
    """
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
    """Combined unit + per-unit price cell — only meaningful for symbol units.

    A symbol unit (BTC, AAPL, …) shows the ticker with its per-unit market
    price ("BTC $95,000"), or the bare ticker when no price is cached yet. USD
    rows have no per-unit price — the value is the Amount directly — so they
    show blank.
    """
    if not unit or unit == "USD":
        return _BLANK
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
    cols,
    col_fields,
    edit_raw,
):
    """Assemble a row record: display cells, per-cell classes, edit metadata.

    `cols` is the group's column layout (Accounts or Assets); cells/fields are
    positioned by its keys so the two groups share the leading slots. The
    left-border accent encodes the asset/liability split by the sign of the
    amount (assets green, liabilities red).
    """
    keys = _col_keys(cols)
    is_liability = amount < 0
    cells = [values.get(k, _BLANK) for k in keys]
    # Mute blank cells so populated data stands out; keep the As-of staleness
    # color where the cell actually carries a date.
    cell_classes = [_MUTED if c == _BLANK else "" for c in cells]
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
        "fields": [col_fields.get(k) for k in keys],
        "edit_raw": edit_raw,
        # Asset/liability left accent. Painted on the first cell via CSS
        # (data-accent) rather than a <tr> border: WebKit is unreliable about
        # row borders in border-collapse tables, especially when the sheet
        # scrolls horizontally.
        "accent_side": "liability" if is_liability else "asset",
    }


def _account_row(a: dict, funding_by_id: dict, account_display: dict, today: date):
    amount = calculations.account_contribution(a)
    due_day = a.get("statement_due_day_of_month")
    pay_ref = a.get("paymentAccountRef")
    funding = funding_by_id.get(a.get("id"))
    values = {
        "institution": a.get("institution") or _BLANK,
        "type": _type_label(a.get("type")) or _BLANK,
        "name": a.get("name") or _BLANK,
        "amount": fmt_money(amount),
        "rewards": _money(a.get("rewards_balance")),
        "limit": _money(a.get("limit")),
        "available": _money(a.get("available")),
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
        "available": _raw(a.get("available")),
        "statement_balance": _raw(a.get("statement_balance")),
        "statement_due_day_of_month": _raw(a.get("statement_due_day_of_month")),
        "paymentAccountRef": _raw(a.get("paymentAccountRef")),
        "minimum_balance": _raw(a.get("minimum_balance")),
    }
    return _make_row(
        values,
        amount,
        a.get("type"),
        a.get("institution") or "",
        today,
        "account",
        a.get("id"),
        _ACCOUNT_COLS,
        _account_col_fields(a),
        edit_raw,
    )


def _asset_row(e: dict, pair: dict | None, linked: str, index: int, today: date):
    is_debt = e.get("kind") == "debt"
    # Per-unit price is `value` for an asset, `balance` (owed per unit) for a
    # debt; amount is the signed subtotal (qty × price, liabilities −).
    price = e.get("balance") if is_debt else e.get("value")
    amount = calculations.asset_contribution(e)
    values = {
        "institution": e.get("institution") or _BLANK,
        "type": _type_label(e.get("type")) or _BLANK,
        "name": e.get("name") or _BLANK,
        "unit_price": _unit_price(e.get("unit") or "USD", price),
        "qty": _fmt_qty(e.get("quantity")),
        "amount": fmt_money(amount),
        "due": e.get("nextDueDate") if is_debt and e.get("nextDueDate") else _BLANK,
        # Linked holding: for a loan, the asset it is secured by; for an asset,
        # the loan(s) secured against it.
        "linked": linked,
        "interest": _fmt_pct(e.get("interestRate")),
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
        "nextDueDate": e.get("nextDueDate") or "",
        "assetRef": _raw(e.get("assetRef")),
        "source": e.get("source") or "",
    }
    return _make_row(
        values,
        amount,
        e.get("type"),
        e.get("institution") or "",
        today,
        "asset",
        index,
        _ASSET_COLS,
        _asset_col_fields(e),
        edit_raw,
    )


def _all_rows(ctx: dict, today: date) -> list[dict]:
    """Build every holding row (accounts then assets), unfiltered."""
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

    account_rows = [
        _account_row(a, funding_by_id, account_display, today) for a in accounts
    ]
    asset_rows = [
        _asset_row(e, equity_by_debt.get(id(e)), _linked(e), idx, today)
        for idx, e in enumerate(assets)
    ]
    return account_rows, asset_rows


def _apply_filters(rows, type_sel, balance_sel, inst_sel):
    if type_sel:
        rows = [r for r in rows if r["type_value"] in type_sel]
    if balance_sel:
        rows = [r for r in rows if r["balance_side"] in balance_sel]
    if inst_sel:
        rows = [r for r in rows if r["institution"] in inst_sel]
    return rows


def _subtotal_cells(cols, label, rows):
    """A group subtotal row (label under Name, sum under Amount) + the sum."""
    total = sum((r["amount"] for r in rows), Decimal("0"))
    cells = [""] * len(cols)
    cells[2] = label  # Name slot
    cells[_AMOUNT_POS] = fmt_money(total)
    return cells, total


def _groups_ctx(account_rows, asset_rows):
    """The two domain groups (headers, rows, subtotals) + the net-worth total."""
    acc_sub, acc_total = _subtotal_cells(_ACCOUNT_COLS, "Accounts", account_rows)
    ast_sub, ast_total = _subtotal_cells(_ASSET_COLS, "Assets", asset_rows)
    groups = [
        {
            "source": "account",
            "label": "Accounts",
            "headers": _col_headers(_ACCOUNT_COLS),
            "right_align": _col_right_align(_ACCOUNT_COLS),
            "rows": account_rows,
            "subtotal": acc_sub,
        },
        {
            "source": "asset",
            "label": "Assets",
            "headers": _col_headers(_ASSET_COLS),
            "right_align": _col_right_align(_ASSET_COLS),
            "rows": asset_rows,
            "subtotal": ast_sub,
        },
    ]
    master = [""] * _NCOLS
    master[2] = "Net worth"
    master[_AMOUNT_POS] = fmt_money(acc_total + ast_total)
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


def _tbody_ctx(account_rows, asset_rows, ctx: dict, **editing) -> dict:
    """Shared context for the tbody partial (page render and edit swaps)."""
    account_ref_options, asset_ref_options = _ref_options(ctx)
    groups, master_total = _groups_ctx(account_rows, asset_rows)
    return {
        "groups": groups,
        "master_total": master_total,
        "ncols": _NCOLS,
        "amount_pos": _AMOUNT_POS,
        "account_type_options": ACCOUNT_TYPE_OPTIONS,
        "asset_type_options": ASSET_TYPE_OPTIONS,
        "account_ref_options": account_ref_options,
        "asset_ref_options": asset_ref_options,
        **editing,
    }


def _render_tbody(snapshot_id, filename, error=None, **editing):
    """Render just the tbody (for cell_edit / update HTMX swaps), edit mode on."""
    ctx = get_common_context(snapshot_id, filename, edit_mode=True)
    account_rows, asset_rows = _all_rows(ctx, date.today())
    tbody = _tbody_ctx(
        account_rows,
        asset_rows,
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

    account_rows, asset_rows = _all_rows(ctx, date.today())
    all_rows = account_rows + asset_rows
    institutions = sorted({r["institution"] for r in all_rows if r["institution"]})
    # Type filter options: the canonical types actually present, in canonical
    # order (not alphabetical), so the dropdown reads sensibly.
    present_types = {r["type_value"] for r in all_rows if r["type_value"]}
    type_values_labels = [
        (v, lbl) for v, lbl in _ALL_TYPE_OPTIONS if v in present_types
    ]

    # --- filters (multi-select, form-submit; mirrors the Assets sheet) ---
    type_sel = [t for t in request.args.getlist("type") if t in present_types]
    balance_sel = [b for b in request.args.getlist("balance") if b in _BALANCE_LABELS]
    inst_sel = [i for i in request.args.getlist("institution") if i in institutions]

    f_accounts = _apply_filters(account_rows, type_sel, balance_sel, inst_sel)
    f_assets = _apply_filters(asset_rows, type_sel, balance_sel, inst_sel)

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

    ctx.update(_tbody_ctx(f_accounts, f_assets, ctx, edit_mode=edit_mode))
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
        # A holding is always denominated in something; blank means USD.
        return (value_raw.upper() or "USD"), None
    if field in ("institution", "source", "nextDueDate"):
        # Free text / ISO date; blank clears to None (repo coerces dates).
        return (value_raw or None), None
    # Numeric money/detail fields go through the shared coercion the Accounts/
    # Assets pages use, so validation and rounding stay identical.
    cmap = ACCOUNTS_COERCION if source == "account" else ASSETS_COERCION
    return coerce_value(field, value_raw, cmap)


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
                update_account(conn, snapshot_id, ref, {field: value})
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
