"""Budget blueprint - unified income/expenses table and CRUD operations."""

from flask import Blueprint, abort, current_app, render_template, request

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
