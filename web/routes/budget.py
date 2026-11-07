"""Budget blueprint - the grouped Budget sheet, Budget vs Actual, and CRUD.

The Budget sheet is a grouped sheet (see grouped_sheet.py), like Holdings: ONE
table split into one band per **category**, in the category taxonomy's order
(entries whose category isn't in the taxonomy follow alphabetically; entries
with none close the table as "Uncategorized"). Each band's heading carries its
Remaining / This month / Per month / Annual subtotals (signed: income positive, expenses
negative); a pinned footer shows the Net across every shown entry. Each
category gets its own band color, cycling a palette in category order so
neighbouring bands differ (Uncategorized is slate). There is no Kind column:
an entry's kind shows in its sign (expenses are negative), and it is set by
the band's "+ Add expense" / "+ Add income" buttons.

Rows are addressed by their position in the full, sort-ordered budget list
(`index`), so edit/delete URLs are unaffected by grouping. Drag-reorder is per
band: the band's local permutation is mapped onto the band's own slots in the
full ordering (grouped_sheet.remap_group_order).
"""

import re
from datetime import date
from decimal import Decimal

from flask import Blueprint, abort, current_app, render_template, request

from fintrack.budget import reconcile
from fintrack.budget import repository as repo_budget
from fintrack.core import filters, tables
from fintrack.core.formatting import fmt_money
from fintrack.core.loader import load_finances_from_db
from fintrack.ledger.repository.categories import get_category_names
from fintrack.networth import calculations

from .common import get_common_context, validate_snapshot
from .crud import (
    BUDGET_COERCION,
    coerce_value,
    handle_delete,
    handle_move,
)
from .grouped_sheet import flat_sheet, group_columns, parse_order, remap_group_order

budget_bp = Blueprint("budget", __name__, url_prefix="/s")

BUDGET_KINDS = calculations.BUDGET_KINDS
RECURRENCE_OPTIONS = calculations.RECURRENCE_OPTIONS

# Column layout (key, header, right_align, span): tables.BUDGET_HEADERS minus
# Kind (the first cell of budget_entry_row, dropped below).
_COLS = [
    ("category", "Category", False, 1),
    ("description", "Description", False, 1),
    ("amount", "Amount", True, 1),
    ("recurrence", "Recurrence", False, 1),
    ("when", "When", True, 1),
    ("remaining", "Remaining", True, 1),
    ("monthly", "This month", True, 1),
    ("per_month", "Per month", True, 1),
    ("annual", "Annual", True, 1),
    ("auto_account", "Auto account", False, 1),
]
_TOOLTIPS = {
    "category": "Budget category",
    "description": "Description of this budget entry",
    "amount": "Per-occurrence amount",
    "recurrence": "How often this entry recurs",
    "when": "Day of month or specific date",
    "remaining": "Prorated amount remaining this month",
    "monthly": "Scheduled in this calendar month (an annual bill due in another month is $0)",
    "per_month": "Average per month: the annual amount ÷ 12",
    "annual": "Annualized total",
    "auto_account": "Account for auto-matching transactions",
}
_COLUMN_META = group_columns(_COLS, _TOOLTIPS)
_NCOLS = len(_COLS)

# The Budget vs Actual sheet (read-only, unbanded); rows map cells by key
# (partials/budget_actual_content.html).
ACTUAL_SHEET = flat_sheet(
    "actual",
    [
        ("entry", "Entry", False, 1),
        ("category", "Category", False, 1),
        ("expected", "Expected", True, 1),
        ("actual", "Actual", True, 1),
        ("delta", "Delta", True, 1),
        ("count", "N", True, 1),
        ("status", "Status", False, 1),
        ("drift", "Drift", True, 1),
        ("last_seen", "Last seen", False, 1),
    ],
    {
        "entry": "Budget entry",
        "category": "Budget category",
        "expected": "Scheduled amount this month (income +, expenses −)",
        "actual": "Realized from linked transactions, net of linked refunds/clawbacks (or the category total for a variable line); income +, expenses −",
        "delta": "Actual minus expected: + is better than budget (less spent or more earned)",
        "count": "Number of linked transactions (refunds/clawbacks excluded)",
        "status": "Matched / over / under / on pace / missing / upcoming / not linked. Mid-month, a variable line is judged against its pace (expected × share of the month gone)",
        "drift": "Linked charge vs budgeted amount (price drift)",
        "last_seen": "Date of the most recent linked transaction (any month)",
    },
)
# The heading label spans Category..When; the Remaining / This month / Per month /
# Annual subtotals sit under their columns.
_LABEL_SPAN = 5
_TOTAL_POS = [5, 6, 7, 8]
# Band colors (partials/grouped_sheet.css palette), cycled in category order.
# Ordered so consecutive hues contrast, including the wrap from last to first.
_CATEGORY_TONES = [
    "blue",
    "amber",
    "violet",
    "emerald",
    "rose",
    "cyan",
    "orange",
    "indigo",
    "lime",
    "fuchsia",
    "teal",
    "pink",
]
_UNCATEGORIZED_TONE = "slate"
_UNCATEGORIZED = "Uncategorized"


def _band_key(category: str) -> str:
    """A stable DOM/URL-safe key for a category band (per-band sort state)."""
    if not category:
        return "uncategorized"
    return "cat-" + (re.sub(r"[^a-z0-9]+", "-", category.lower()).strip("-") or "x")


def _sheet_ctx(
    budget: list[dict],
    shown: list[int],
    categories: list[str],
    ctx: dict,
    edit_mode: bool,
    filters_active: bool = False,
) -> dict:
    """Template context for the grouped budget table: the category bands for
    the `shown` entries (indices into `budget`), the Net footer, and the column
    metadata."""
    by_category: dict[str, list[dict]] = {}
    for i in shown:
        e = budget[i]
        cells, figures = tables.budget_entry_row(
            e,
            ctx["year"],
            ctx["month"],
            ctx["day"],
            account_display_by_id=ctx["account_display_by_id"],
        )
        by_category.setdefault(e.get("category") or "", []).append(
            {
                "index": i,
                "kind": e.get("kind", "income"),
                "cells": cells[1:],  # drop Kind
                "figures": figures,
            }
        )

    # Every category in band order: the taxonomy, then any others the budget
    # uses. Colors index into this full list (not just the shown bands), so a
    # category keeps its color while filters hide its neighbours.
    known = set(categories)
    all_categories = categories + sorted(
        {e.get("category") for e in budget if e.get("category")} - known
    )
    tone_of = {
        c: _CATEGORY_TONES[i % len(_CATEGORY_TONES)]
        for i, c in enumerate(all_categories)
    }
    order = [c for c in all_categories if c in by_category] + (
        [""] if "" in by_category else []
    )
    keys: set[str] = set()
    groups = []
    for category in order:
        rows = by_category[category]
        incomes = sum(r["kind"] == "income" for r in rows)
        key = _band_key(category)
        while key in keys:  # two categories that slugify alike
            key += "-"
        keys.add(key)
        groups.append(
            {
                "key": key,
                "category": category,
                "label": category or _UNCATEGORIZED,
                "tone": tone_of.get(category, _UNCATEGORIZED_TONE),
                # The add row leads with the band's majority kind.
                "first_kind": "income" if incomes * 2 > len(rows) else "expense",
                "rows": rows,
                "totals": [
                    fmt_money(sum((r["figures"][j] for r in rows), Decimal(0)))
                    for j in range(len(_TOTAL_POS))
                ],
                **_COLUMN_META,
            }
        )

    all_rows = [r for g in groups for r in g["rows"]]
    net = [""] * _NCOLS
    net[0] = "Net"
    for j, pos in enumerate(_TOTAL_POS):
        net[pos] = fmt_money(sum((r["figures"][j] for r in all_rows), Decimal(0)))

    return {
        "groups": groups,
        "footer_rows": [net] if all_rows else [],
        "filters_active": filters_active,
        "max_cols": _NCOLS,
        "label_span": _LABEL_SPAN,
        "total_pos": _TOTAL_POS,
        "edit_mode": edit_mode,
        "categories": categories,
        "recurrence_options": RECURRENCE_OPTIONS,
        "account_display_by_id": ctx["account_display_by_id"],
    }


def _category_names() -> list[str]:
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        return get_category_names(conn)


def _render_tbody(snapshot_id: int, filename: str, edit_mode=True, **editing):
    """Re-render the whole grouped table body (colgroup + every band + footer)
    for an edit swap (target #budget-table). Shows every entry: an edit swap
    doesn't carry the page's filters."""
    ctx = get_common_context(snapshot_id, filename, edit_mode)
    budget = ctx["budget"]
    sheet = _sheet_ctx(
        budget, list(range(len(budget))), _category_names(), ctx, edit_mode
    )
    return render_template(
        "partials/budget_tbody.html", filename=filename, **sheet, **editing
    )


@budget_bp.route("/<filename>/budget")
def budget_view(filename: str):
    snapshot_id = validate_snapshot(filename)
    edit_mode = request.args.get("edit") == "1"
    ctx = get_common_context(snapshot_id, filename, edit_mode)
    ctx["active_tab"] = "budget"
    include_kinds = (
        request.args.getlist("include_kind") or request.args.getlist("kind") or []
    )
    include_categories = (
        request.args.getlist("include") or request.args.getlist("i") or []
    )
    include_recurrence = (
        request.args.getlist("include_recurrence")
        or request.args.getlist("recurrence")
        or []
    )
    full_budget = ctx["budget"]
    filtered = filters.apply_budget_filters(
        full_budget,
        include_kinds=include_kinds or None,
        include_categories=include_categories or None,
        include_recurrence=include_recurrence or None,
    )
    # Filters return the same entry dicts; map them back to their positions in
    # the full list (by identity — two identical entries must stay distinct).
    kept = {id(e) for e in filtered}
    shown = [i for i, e in enumerate(full_budget) if id(e) in kept]
    categories = _category_names()
    include_kinds_set = {k.lower() for k in include_kinds}
    ctx["include_kinds"] = [k for k in BUDGET_KINDS if k in include_kinds_set]
    ctx["include_categories"] = [c for c in categories if c in set(include_categories)]
    ctx["include_recurrence"] = [
        r for r in RECURRENCE_OPTIONS if r in set(include_recurrence)
    ]
    ctx["budget_kinds"] = BUDGET_KINDS
    ctx.update(
        _sheet_ctx(
            full_budget,
            shown,
            categories,
            ctx,
            edit_mode,
            filters_active=len(shown) < len(full_budget),
        )
    )
    return render_template("budget.html", **ctx)


# Status filter options for the Budget vs Actual page, in display order
# ("inactive" entries are never shown, so it isn't offered).
ACTUAL_STATUSES = {
    "matched": "Matched",
    "over": "Over",
    "under": "Under",
    "on_pace": "On pace",
    "missing": "Missing",
    "upcoming": "Upcoming",
    "unlinked": "Not linked",
}


def _valid_month(year: int, month: int, default: date) -> date:
    try:
        return date(year, month, 1)
    except ValueError:
        return default


@budget_bp.route("/<filename>/budget/actual")
def actual_view(filename: str):
    """Per-entry budget-vs-actual for one month (issue #53), on its own page
    (issue #73): expected vs realized from linked transactions, with
    missed/upcoming/drift flags. Month paging (``?year=&month=``, default: now)
    and htmx partial swaps mirror the Transactions sheet. In edit mode, link
    suggestions for the month sit under the entry they'd realize, with
    Apply / Dismiss actions (issue #70)."""
    return _render_actual(filename, validate_snapshot(filename))


def _suggestion_action(filename: str, action):
    """Run a link-suggestion action, then re-render the page for the month and
    filters in the query string (the buttons post to the page's own query)."""
    snapshot_id = validate_snapshot(filename)
    try:
        txn_id = int(request.form["transaction_id"])
        entry_ref = int(request.form["budget_entry_ref"])
    except (KeyError, ValueError):
        return "", 422
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        try:
            action(conn, snapshot_id, txn_id, entry_ref)
        except ValueError:  # incl. SnapshotMismatch
            return "", 422
    return _render_actual(filename, snapshot_id)


@budget_bp.route("/<filename>/budget/actual/link", methods=["POST"])
def actual_link(filename: str):
    """Apply a link suggestion."""
    return _suggestion_action(filename, reconcile.link_transaction)


@budget_bp.route("/<filename>/budget/actual/dismiss", methods=["POST"])
def actual_dismiss(filename: str):
    """Dismiss a link suggestion so it isn't proposed again."""
    return _suggestion_action(filename, reconcile.dismiss_suggestion)


def _render_actual(filename: str, snapshot_id: int):
    edit_mode = request.args.get("edit") == "1"
    ctx = get_common_context(snapshot_id, filename, edit_mode)
    ctx["active_tab"] = "budget_actual"
    current = date(ctx["year"], ctx["month"], 1)
    viewed = _valid_month(
        request.args.get("year", current.year, type=int),
        request.args.get("month", current.month, type=int),
        current,
    )
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        actuals = reconcile.budget_actuals(
            conn, snapshot_id, year=viewed.year, month=viewed.month
        )
        suggestions = reconcile.suggest_links(
            conn, snapshot_id, year=viewed.year, month=viewed.month
        )
    active = [a for a in actuals if a.status != "inactive"]

    include_kinds = [k for k in BUDGET_KINDS if k in request.args.getlist("kind")]
    categories = sorted(
        {a.entry["category"] for a in active if a.entry.get("category")}
    )
    include_categories = [
        c for c in categories if c in request.args.getlist("category")
    ]
    include_statuses = [
        s for s in ACTUAL_STATUSES if s in request.args.getlist("status")
    ]
    shown = [
        a
        for a in active
        if (not include_kinds or a.entry.get("kind", "income") in include_kinds)
        and (not include_categories or a.entry.get("category") in include_categories)
        and (not include_statuses or a.status in include_statuses)
    ]

    # Net totals of the signed figures (income +, expenses −): a positive delta
    # is a surplus against budget, a negative one a shortfall.
    def _net(attr: str) -> Decimal:
        return sum((getattr(a, f"signed_{attr}") for a in shown), Decimal(0))

    net_expected, net_actual = _net("expected"), _net("actual")

    # Suggestions grouped under the entry they'd realize, best first
    # (suggest_links already sorts by score). Only shown entries host them.
    shown_refs = {a.entry_ref for a in shown}
    suggestions_by_entry: dict[int, list] = {}
    for sug in suggestions:
        if sug.entry_ref in shown_refs:
            suggestions_by_entry.setdefault(sug.entry_ref, []).append(sug)

    prev = date(viewed.year - (viewed.month == 1), (viewed.month - 2) % 12 + 1, 1)
    nxt = date(viewed.year + (viewed.month == 12), viewed.month % 12 + 1, 1)
    ctx.update(
        budget_actuals=shown,
        missing_count=sum(a.status == "missing" for a in active),
        drift_count=sum(bool(a.drift_amount) for a in active),
        suggestions_by_entry=suggestions_by_entry,
        suggestion_count=sum(len(v) for v in suggestions_by_entry.values()),
        self_query=request.query_string.decode(),
        net_expected=net_expected,
        net_actual=net_actual,
        net_delta=net_actual - net_expected,
        year=viewed.year,
        month=viewed.month,
        month_label=f"{viewed:%b %Y}",
        is_latest=viewed >= current,
        prev_year=prev.year,
        prev_month=prev.month,
        next_year=nxt.year,
        next_month=nxt.month,
        latest_year=current.year,
        latest_month=current.month,
        budget_kinds=BUDGET_KINDS,
        actual_categories=categories,
        actual_statuses=ACTUAL_STATUSES,
        include_kinds=include_kinds,
        include_categories=include_categories,
        include_statuses=include_statuses,
    )
    template = (
        "partials/budget_actual_content.html"
        if request.headers.get("HX-Request")
        else "budget_actual.html"
    )
    return render_template(template, **ctx)


@budget_bp.route("/<filename>/budget/delete-btn/<int:index>")
def delete_btn(filename: str, index: int):
    snapshot_id = validate_snapshot(filename)
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        data = load_finances_from_db(conn, snapshot_id)
    budget = data.get("budget") or []
    if index < 0 or index >= len(budget):
        abort(404)
    kind = budget[index].get("kind", "income")
    return render_template(
        "partials/budget_delete_icon.html",
        filename=filename,
        kind=kind,
        index=index,
        edit_mode=True,
    )


@budget_bp.route("/<filename>/budget/delete-confirm/<int:index>")
def delete_confirm(filename: str, index: int):
    snapshot_id = validate_snapshot(filename)
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        data = load_finances_from_db(conn, snapshot_id)
    budget = data.get("budget") or []
    if index < 0 or index >= len(budget):
        abort(404)
    kind = budget[index].get("kind", "income")
    return render_template(
        "partials/budget_delete_confirm.html",
        filename=filename,
        kind=kind,
        index=index,
    )


@budget_bp.route("/<filename>/budget/delete/<int:index>", methods=["POST"])
def delete(filename: str, index: int):
    snapshot_id = validate_snapshot(filename)
    engine = current_app.config["engine"]
    return handle_delete(
        lambda conn: repo_budget.delete_budget_entry(conn, snapshot_id, index),
        engine,
    )


@budget_bp.route("/<filename>/budget/move/<int:index>", methods=["POST"])
def move(filename: str, index: int):
    snapshot_id = validate_snapshot(filename)
    engine = current_app.config["engine"]
    return handle_move(
        lambda conn, d: repo_budget.move_budget_entry(conn, snapshot_id, index, d),
        engine,
    )


@budget_bp.route("/<filename>/budget/reorder", methods=["POST"])
def reorder(filename: str):
    """Persist a drag reorder within one category band (``?category=``; empty
    for Uncategorized). The posted ``order`` permutes the band's own rows; it is
    mapped onto the band's slots in the full ordering, leaving other bands'
    rows in place."""
    snapshot_id = validate_snapshot(filename)
    category = request.args.get("category", "")
    local_order = parse_order(request.form.get("order", ""))
    if not local_order:
        return "", 422
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        budget = load_finances_from_db(conn, snapshot_id).get("budget") or []
        full = remap_group_order(
            budget, lambda e: (e.get("category") or "") == category, local_order
        )
        if full is None:
            return "", 422
        try:
            repo_budget.reorder_budget_entries(conn, snapshot_id, full)
        except ValueError:
            return "", 422
    return "", 204


@budget_bp.route("/<filename>/budget/add", methods=["POST"])
def add(filename: str):
    snapshot_id = validate_snapshot(filename)
    kind = request.form.get("kind", "expense").strip()
    if kind not in ("income", "expense"):
        kind = "expense"
    entry = {
        "kind": kind,
        "description": "New entry",
        "amount": 0.0,
        "recurrence": "monthly",
    }
    engine = current_app.config["engine"]
    try:
        with engine.connect() as conn:
            # Added from a category band: file the stub under that band's
            # category (a taxonomy category, or one the snapshot already uses).
            category = request.form.get("category", "").strip()
            if category:
                budget = load_finances_from_db(conn, snapshot_id).get("budget") or []
                if category in get_category_names(conn) or any(
                    e.get("category") == category for e in budget
                ):
                    entry["category"] = category
            repo_budget.add_budget_entry(conn, snapshot_id, entry)
    except ValueError:
        return "", 422
    resp = current_app.make_response("")
    resp.headers["HX-Refresh"] = "true"
    return resp


@budget_bp.route("/<filename>/budget/cell/<int:index>")
def cell_edit(filename: str, index: int):
    field = request.args.get("field", "description")
    snapshot_id = validate_snapshot(filename)
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        data = load_finances_from_db(conn, snapshot_id)
    budget = data.get("budget") or []
    if index < 0 or index >= len(budget):
        abort(404)
    entry = budget[index]

    if request.args.get("display") == "1":
        return _render_tbody(snapshot_id, filename, edit_mode=True)

    if field == "when":
        return _render_tbody(
            snapshot_id,
            filename,
            edit_mode=True,
            editing_index=index,
            editing_field=field,
            editing_value="",
            editing_when_recurrence=entry.get("recurrence"),
            editing_when_month=entry.get("month"),
            editing_when_day=entry.get("dayOfMonth")
            if entry.get("dayOfMonth") is not None
            else entry.get("dayOfYear"),
            editing_when_continuous=entry.get("continuous", False),
        )

    value = entry.get(field, "")
    if value is None:
        value = ""
    if field == "autoAccountRef" and value != "":
        value = str(value)

    return _render_tbody(
        snapshot_id,
        filename,
        edit_mode=True,
        editing_index=index,
        editing_field=field,
        editing_value=value,
    )


@budget_bp.route("/<filename>/budget/update/<int:index>", methods=["POST"])
def update(filename: str, index: int):
    snapshot_id = validate_snapshot(filename)
    field = request.form.get("field", "description").strip()
    value_raw = request.form.get("value", "").strip()

    if not field:
        return _render_tbody(snapshot_id, filename, edit_mode=True), 422

    value, error = coerce_value(field, value_raw, BUDGET_COERCION)
    if error:
        return _render_tbody(snapshot_id, filename, edit_mode=True), 422

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        data = load_finances_from_db(conn, snapshot_id)
    budget = data.get("budget") or []
    if index < 0 or index >= len(budget):
        abort(404)
    if budget[index].get(field) == value:
        return _render_tbody(
            snapshot_id,
            filename,
            edit_mode=True,
            updated_index=index,
            updated_field=field,
        )

    try:
        with engine.connect() as conn:
            repo_budget.update_budget_entry(conn, snapshot_id, index, {field: value})
    except ValueError:
        return _render_tbody(snapshot_id, filename, edit_mode=True), 422

    return _render_tbody(
        snapshot_id,
        filename,
        edit_mode=True,
        updated_index=index,
        updated_field=field,
    )


@budget_bp.route("/<filename>/budget/when/<int:index>", methods=["POST"])
def when_update(filename: str, index: int):
    snapshot_id = validate_snapshot(filename)
    engine = current_app.config["engine"]

    month_raw = request.form.get("month", "").strip()
    day_raw = request.form.get("dayOfMonth", "").strip()
    continuous_raw = request.form.get("continuous", "").strip().lower()

    updates = {}
    delete_keys = []
    if month_raw:
        try:
            updates["month"] = int(month_raw)
        except ValueError:
            return _render_tbody(snapshot_id, filename, edit_mode=True), 422
    if day_raw:
        try:
            updates["dayOfMonth"] = int(day_raw)
        except ValueError:
            return _render_tbody(snapshot_id, filename, edit_mode=True), 422
    if continuous_raw == "true":
        updates["continuous"] = True
    elif continuous_raw == "false":
        delete_keys.append("continuous")

    if not updates and not delete_keys:
        return _render_tbody(snapshot_id, filename, edit_mode=True)

    try:
        with engine.connect() as conn:
            repo_budget.update_budget_entry(
                conn, snapshot_id, index, updates, delete_keys
            )
    except ValueError:
        return _render_tbody(snapshot_id, filename, edit_mode=True), 422

    return _render_tbody(
        snapshot_id,
        filename,
        edit_mode=True,
        updated_index=index,
        updated_field="when",
    )
