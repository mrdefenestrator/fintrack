"""Accounts blueprint - accounts table and CRUD operations."""

from datetime import date

from flask import Blueprint, abort, current_app, make_response, render_template, request
from sqlalchemy.exc import IntegrityError

from fintrack.core import filters, tables
from fintrack.networth import calculations
from fintrack.accounts.balance_history import get_balance_history
from fintrack.core.loader import load_finances_from_db
from fintrack.accounts import repository as repo_accounts

from .common import (
    account_field_editable,
    drop_separator_rows,
    get_common_context,
    validate_snapshot,
)
from .crud import (
    ACCOUNTS_COERCION,
    coerce_value,
    handle_move,
)

accounts_bp = Blueprint("accounts", __name__, url_prefix="/s")

ACCOUNT_TYPES = calculations.ACCOUNT_TYPES

_SPARK_W, _SPARK_H = 72, 16


def _history_meta(conn, accs: list) -> dict:
    """Per-account sparkline points, as-of staleness, and reconciliation note."""
    today = date.today()
    out: dict = {}
    for acc in accs:
        points = get_balance_history(conn, acc["id"], limit=12)
        if not points:
            out[acc["id"]] = None
            continue
        values = [float(p["balance"]) for p in points]
        if len(values) == 1:
            values = values * 2
        lo, hi = min(values), max(values)
        span = (hi - lo) or 1.0
        step = _SPARK_W / (len(values) - 1)
        svg_points = " ".join(
            f"{i * step:.1f},{_SPARK_H - 1 - ((v - lo) / span) * (_SPARK_H - 2):.1f}"
            for i, v in enumerate(values)
        )
        last = points[-1]
        age = (today - last["as_of"]).days
        color = (
            "text-emerald-500"
            if age <= 35
            else ("text-amber-500" if age <= 95 else "text-red-500")
        )
        out[acc["id"]] = {
            "points": svg_points,
            "as_of": last["as_of"],
            "age": age,
            "color": color,
            "note": last.get("note"),
        }
    return out


def _duplicate_account_message(name: str, institution: str | None) -> str:
    """User-facing message for a unique constraint violation on accounts.

    The constraint is (snapshot_id, institution, name): two accounts can
    share a name as long as their institution differs.
    """
    if institution:
        return (
            f'An account named "{name}" already exists for institution "{institution}".'
        )
    return f'An account named "{name}" already exists.'


def _render_tbody(
    snapshot_id: int,
    filename: str,
    edit_mode=True,
    updated_account_id=None,
    updated_field=None,
    editing_account_id=None,
    editing_field=None,
    editing_value=None,
    error=None,
):
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        data = load_finances_from_db(conn, snapshot_id)
    accs = data.get("accounts") or []
    budget = data.get("budget") or []
    today = date.today()
    n2 = calculations.liquid_minus_cc(accs)
    account_display_by_id = tables._account_display_by_id(accs)
    headers, rows = tables._build_accounts_table(
        accs, n2, account_display_by_id=account_display_by_id
    )
    rows = drop_separator_rows(rows)
    funding_by_id = {
        acc["id"]: calculations.account_funding_needed(
            acc, accs, budget, today, default_reserve=0
        )
        for acc in accs
        if calculations._ACCOUNT_TYPE_TO_CALCULATION.get(acc.get("type")) == "liquid"
    }
    with engine.connect() as conn:
        history_by_id = _history_meta(conn, accs)
    rows[-1] += ["-", "-", "-"]
    edit_rows = list(zip(accs, rows))

    return render_template(
        "partials/accounts_tbody.html",
        history_by_id=history_by_id,
        filename=filename,
        edit_mode=edit_mode,
        edit_rows=edit_rows,
        rows=rows,
        updated_account_id=updated_account_id,
        updated_field=updated_field,
        account_types=ACCOUNT_TYPES,
        account_display_by_id=account_display_by_id,
        editing_account_id=editing_account_id,
        editing_field=editing_field,
        editing_value=editing_value,
        funding_by_id=funding_by_id,
        error=error,
    )


@accounts_bp.route("/<filename>/accounts")
def accounts_view(filename: str):
    snapshot_id = validate_snapshot(filename)
    edit_mode = request.args.get("edit") == "1"
    ctx = get_common_context(snapshot_id, filename, edit_mode)
    ctx["active_tab"] = "accounts"
    ctx["sort_col"] = request.args.get("sort_col", "")
    ctx["sort_dir"] = request.args.get("sort_dir", "")
    include_types = (
        request.args.getlist("include_type") or request.args.getlist("type") or []
    )
    accs = filters.filter_accounts_by_type(ctx["accounts"], include_types or None)
    include_types_set = set(t.lower() for t in include_types)
    n2 = calculations.liquid_minus_cc(accs)
    account_display_by_id = ctx["account_display_by_id"]
    ctx["headers"], ctx["rows"] = tables._build_accounts_table(
        accs, n2, account_display_by_id=account_display_by_id
    )
    ctx["rows"] = drop_separator_rows(ctx["rows"])
    ctx["include_types"] = [t for t in ACCOUNT_TYPES if t in include_types_set]
    ctx["account_types"] = ACCOUNT_TYPES
    ctx["accounts_raw"] = accs
    ctx["edit_rows"] = list(zip(accs, ctx["rows"]))

    all_accounts = ctx["accounts"]
    budget = ctx["budget"]
    today = date.today()
    ctx["funding_by_id"] = {
        acc["id"]: calculations.account_funding_needed(
            acc, all_accounts, budget, today, default_reserve=0
        )
        for acc in all_accounts
        if calculations._ACCOUNT_TYPE_TO_CALCULATION.get(acc.get("type")) == "liquid"
    }
    ctx["headers"] += ["Reserve", "Funding Needed", "History"]
    ctx["rows"][-1] += ["-", "-", "-"]
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        ctx["history_by_id"] = _history_meta(conn, accs)

    return render_template("accounts.html", **ctx)


@accounts_bp.route("/<filename>/accounts/cell/<int:account_id>")
def cell_edit(filename: str, account_id: int):
    field = request.args.get("field", "name")
    snapshot_id = validate_snapshot(filename)
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        data = load_finances_from_db(conn, snapshot_id)
    accs = data.get("accounts") or []
    acc = next((a for a in accs if a.get("id") == account_id), None)
    if not acc:
        abort(404)

    if request.args.get("display") == "1":
        return _render_tbody(snapshot_id, filename, edit_mode=True)

    if not account_field_editable(acc, field):
        return _render_tbody(snapshot_id, filename, edit_mode=True)

    value = acc.get(field, "")
    if value is None:
        value = ""

    return _render_tbody(
        snapshot_id,
        filename,
        edit_mode=True,
        editing_account_id=account_id,
        editing_field=field,
        editing_value=value,
    )


@accounts_bp.route("/<filename>/accounts/update/<int:account_id>", methods=["POST"])
def update(filename: str, account_id: int):
    snapshot_id = validate_snapshot(filename)
    field = request.form.get("field", "name").strip()
    value_raw = request.form.get("value", "").strip()

    if not field:
        return _render_tbody(snapshot_id, filename, edit_mode=True), 422

    engine = current_app.config["engine"]

    def _get_account():
        with engine.connect() as conn:
            data = load_finances_from_db(conn, snapshot_id)
        return next(
            (a for a in (data.get("accounts") or []) if a.get("id") == account_id),
            None,
        )

    acc = _get_account()
    if acc is not None and not account_field_editable(acc, field):
        return _render_tbody(
            snapshot_id,
            filename,
            edit_mode=True,
            updated_account_id=account_id,
            updated_field=field,
        )

    value, error = coerce_value(field, value_raw, ACCOUNTS_COERCION)
    if error:
        return _render_tbody(snapshot_id, filename, edit_mode=True), 422

    acc = _get_account()
    if acc is not None and acc.get(field) == value:
        return _render_tbody(
            snapshot_id,
            filename,
            edit_mode=True,
            updated_account_id=account_id,
            updated_field=field,
        )

    with engine.connect() as conn:
        try:
            repo_accounts.update_account(conn, snapshot_id, account_id, {field: value})
        except ValueError:
            return _render_tbody(snapshot_id, filename, edit_mode=True), 422
        except IntegrityError:
            conn.rollback()
            new_name = value if field == "name" else (acc or {}).get("name", "")
            new_institution = (
                value if field == "institution" else (acc or {}).get("institution")
            )
            return (
                _render_tbody(
                    snapshot_id,
                    filename,
                    edit_mode=True,
                    error=_duplicate_account_message(new_name, new_institution),
                ),
                422,
            )

    return _render_tbody(
        snapshot_id,
        filename,
        edit_mode=True,
        updated_account_id=account_id,
        updated_field=field,
    )


@accounts_bp.route("/<filename>/accounts/add", methods=["POST"])
def add(filename: str):
    snapshot_id = validate_snapshot(filename)
    name = request.form.get("name", "").strip()
    acc_type = request.form.get("type", "checking").strip() or "checking"
    account = {"name": name or "New account", "type": acc_type}
    if acc_type == "credit_card":
        try:
            account["limit"] = float(request.form.get("limit") or 0)
            account["available"] = float(request.form.get("available") or 0)
            for key in ("rewards_balance", "statement_balance"):
                v = request.form.get(key, "").strip()
                if v:
                    account[key] = float(v)
            due_day = request.form.get("statement_due_day_of_month", "").strip()
            if due_day:
                account["statement_due_day_of_month"] = int(due_day)
        except ValueError:
            return "", 422
        pay_ref_raw = request.form.get("paymentAccountRef", "").strip()
        if pay_ref_raw:
            try:
                account["paymentAccountRef"] = int(pay_ref_raw)
            except ValueError:
                pass
    else:
        try:
            account["balance"] = float(request.form.get("balance") or 0)
        except ValueError:
            account["balance"] = 0
    for key in ("institution", "asOfDate"):
        v = request.form.get(key, "").strip()
        if v:
            account[key] = v
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        try:
            new_id = repo_accounts.add_account(conn, snapshot_id, account)
        except ValueError:
            return "", 422
        except IntegrityError:
            conn.rollback()
            # The add row's form targets #accounts-add-row with hx-swap
            # beforebegin, so this response must retarget itself onto the
            # tbody to show the error banner instead of getting inserted as
            # a stray row above the add row.
            resp = make_response(
                _render_tbody(
                    snapshot_id,
                    filename,
                    edit_mode=True,
                    error=_duplicate_account_message(
                        account["name"], account.get("institution")
                    ),
                ),
                422,
            )
            resp.headers["HX-Retarget"] = "#accounts-tbody"
            resp.headers["HX-Reswap"] = "innerHTML"
            return resp
    with engine.connect() as conn:
        data = load_finances_from_db(conn, snapshot_id)
    accs = data.get("accounts") or []
    acc = next((a for a in accs if a.get("id") == new_id), None)
    if not acc:
        abort(404)
    n2 = calculations.liquid_minus_cc(accs)
    account_display_by_id = tables._account_display_by_id(accs)
    _, rows = tables._build_accounts_table(
        accs, n2, account_display_by_id=account_display_by_id
    )
    idx = next((i for i, a in enumerate(accs) if a.get("id") == new_id), -1)
    new_row = rows[idx] if 0 <= idx < len(rows) else []
    return render_template(
        "partials/accounts_row_display.html",
        filename=filename,
        account_id=new_id,
        account=acc,
        row_cells=new_row,
        account_types=ACCOUNT_TYPES,
        account_display_by_id=account_display_by_id,
    )


@accounts_bp.route("/<filename>/accounts/delete-btn/<int:account_id>")
def delete_btn(filename: str, account_id: int):
    return render_template(
        "partials/accounts_delete_btn.html",
        filename=filename,
        account_id=account_id,
    )


@accounts_bp.route("/<filename>/accounts/delete-confirm/<int:account_id>")
def delete_confirm(filename: str, account_id: int):
    return render_template(
        "partials/accounts_delete_confirm.html",
        filename=filename,
        account_id=account_id,
    )


@accounts_bp.route("/<filename>/accounts/delete/<int:account_id>", methods=["POST"])
def delete(filename: str, account_id: int):
    snapshot_id = validate_snapshot(filename)
    engine = current_app.config["engine"]
    try:
        with engine.connect() as conn:
            repo_accounts.delete_account(conn, snapshot_id, account_id)
    except ValueError:
        return "", 422
    return ""


@accounts_bp.route("/<filename>/accounts/move/<int:account_id>", methods=["POST"])
def move(filename: str, account_id: int):
    snapshot_id = validate_snapshot(filename)
    engine = current_app.config["engine"]
    return handle_move(
        lambda conn, d: repo_accounts.move_account(conn, snapshot_id, account_id, d),
        engine,
    )
