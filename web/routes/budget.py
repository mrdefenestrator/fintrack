"""Budget blueprint - unified income/expenses table and CRUD operations."""

from datetime import date
from decimal import Decimal

from flask import Blueprint, abort, current_app, render_template, request

from fintrack.budget import reconcile
from fintrack.budget import repository as repo_budget
from fintrack.core import filters, tables
from fintrack.core.loader import load_finances_from_db
from fintrack.ledger.repository.categories import get_category_names
from fintrack.networth import calculations

from .common import drop_separator_rows, get_common_context, validate_snapshot
from .crud import (
    BUDGET_COERCION,
    coerce_value,
    handle_delete,
    handle_move,
    handle_reorder,
)

budget_bp = Blueprint("budget", __name__, url_prefix="/s")

BUDGET_KINDS = calculations.BUDGET_KINDS
RECURRENCE_OPTIONS = calculations.RECURRENCE_OPTIONS


def _render_tbody(
    snapshot_id: int,
    filename: str,
    edit_mode=True,
    updated_index=None,
    updated_field=None,
    editing_index=None,
    editing_field=None,
    editing_value=None,
    editing_when_recurrence=None,
    editing_when_month=None,
    editing_when_day=None,
    editing_when_continuous=False,
):
    ctx = get_common_context(snapshot_id, filename, edit_mode)
    budget = ctx["budget"]
    _headers, rows = tables._build_budget_table(
        budget,
        ctx["year"],
        ctx["month"],
        ctx["day"],
        account_display_by_id=ctx["account_display_by_id"],
    )
    rows = drop_separator_rows(rows)

    data_rows = rows[: len(budget)] if rows else []
    budget_edit_rows = [
        (budget[i].get("kind", "income"), i, data_rows[i]) for i in range(len(budget))
    ]

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        categories = get_category_names(conn)

    return render_template(
        "partials/budget_tbody.html",
        filename=filename,
        edit_mode=edit_mode,
        budget_edit_rows=budget_edit_rows,
        rows=rows,
        updated_index=updated_index,
        updated_field=updated_field,
        categories=categories,
        recurrence_options=RECURRENCE_OPTIONS,
        account_display_by_id=ctx["account_display_by_id"],
        editing_index=editing_index,
        editing_field=editing_field,
        editing_value=editing_value,
        editing_when_recurrence=editing_when_recurrence,
        editing_when_month=editing_when_month,
        editing_when_day=editing_when_day,
        editing_when_continuous=editing_when_continuous,
    )


@budget_bp.route("/<filename>/budget")
def budget_view(filename: str):
    snapshot_id = validate_snapshot(filename)
    edit_mode = request.args.get("edit") == "1"
    ctx = get_common_context(snapshot_id, filename, edit_mode)
    ctx["active_tab"] = "budget"
    ctx["sort_col"] = request.args.get("sort_col", "")
    ctx["sort_dir"] = request.args.get("sort_dir", "")
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
    budget = filters.apply_budget_filters(
        ctx["budget"],
        include_kinds=include_kinds or None,
        include_categories=include_categories or None,
        include_recurrence=include_recurrence or None,
    )
    include_kinds_set = {k.lower() for k in include_kinds}
    ctx["headers"], ctx["rows"] = tables._build_budget_table(
        budget,
        ctx["year"],
        ctx["month"],
        ctx["day"],
        account_display_by_id=ctx["account_display_by_id"],
    )
    ctx["rows"] = drop_separator_rows(ctx["rows"])
    full_budget = ctx["budget"]
    budget_global = [full_budget.index(e) for e in budget] if budget else []
    data_rows = ctx["rows"][: len(budget)] if ctx["rows"] else []
    ctx["budget_edit_rows"] = [
        (budget[i].get("kind", "income"), budget_global[i], data_rows[i])
        for i in range(len(budget))
    ]
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        categories = get_category_names(conn)
    ctx["include_kinds"] = [k for k in BUDGET_KINDS if k in include_kinds_set]
    ctx["include_categories"] = [c for c in categories if c in set(include_categories)]
    ctx["include_recurrence"] = [
        r for r in RECURRENCE_OPTIONS if r in set(include_recurrence)
    ]
    ctx["budget_kinds"] = BUDGET_KINDS
    ctx["categories"] = categories
    ctx["recurrence_options"] = RECURRENCE_OPTIONS
    return render_template("budget.html", **ctx)


# Status filter options for the Budget vs Actual page, in display order
# ("inactive" entries are never shown, so it isn't offered).
ACTUAL_STATUSES = {
    "matched": "Matched",
    "over": "Over",
    "under": "Under",
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

    # Net totals, signed income - expenses: a positive delta is a surplus
    # against budget, a negative one a shortfall.
    def _net(attr: str) -> Decimal:
        return sum(
            (
                getattr(a, attr)
                if a.entry.get("kind") == "income"
                else -getattr(a, attr)
                for a in shown
            ),
            Decimal(0),
        )

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
    snapshot_id = validate_snapshot(filename)
    engine = current_app.config["engine"]
    return handle_reorder(
        lambda conn, order: repo_budget.reorder_budget_entries(
            conn, snapshot_id, order
        ),
        engine,
    )


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
