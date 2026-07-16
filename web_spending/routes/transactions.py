from datetime import date
from decimal import Decimal

from flask import Blueprint, Response, current_app, render_template, request
from sqlalchemy import select

from spending.models import transactions as txn_table
from spending.repository.accounts import list_accounts
from spending.repository.aggregations import base_transaction_query
from spending.repository.categories import get_category_names
from spending.repository.corrections import apply_transaction_correction
from spending.repository.merchants import set_merchant_category
from spending.repository.transactions import get_transactions

bp = Blueprint("transactions", __name__)


@bp.route("/transactions")
def index():
    today = date.today()
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)
    category = request.args.get("category")
    account_id = request.args.get("account_id", type=int)
    search = request.args.get("search")
    status = request.args.get("status")
    all_months = request.args.get("all_months") == "true"
    sort = request.args.get("sort") or None
    sort_dir = request.args.get("dir") or None

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        txns = get_transactions(
            conn,
            year=None if all_months else year,
            month=None if all_months else month,
            category=category,
            account_id=account_id,
            search=search,
            status=status,
            sort=sort,
            sort_dir=sort_dir,
        )
        accounts = list_accounts(conn)
        categories = get_category_names(conn)

    txn_count = len(txns)
    txn_total = sum((t["amount"] for t in txns), Decimal("0"))

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    template = (
        "partials/transactions_content.html"
        if request.headers.get("HX-Request")
        else "transactions.html"
    )
    return render_template(
        template,
        active_tab="transactions",
        transactions=txns,
        accounts=accounts,
        categories=categories,
        year=year,
        month=month,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
        selected_category=category,
        selected_account=account_id,
        search=search or "",
        selected_status=status,
        all_months=all_months,
        sort=sort,
        dir=sort_dir,
        txn_count=txn_count,
        txn_total=txn_total,
    )


@bp.route("/transactions/<int:txn_id>/edit-category", methods=["GET"])
def edit_category_form(txn_id):
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        categories = get_category_names(conn)
        subq = base_transaction_query().where(txn_table.c.id == txn_id).subquery()
        row = conn.execute(select(subq)).fetchone()
    current_category = dict(row._mapping)["category"] if row else None
    return render_template(
        "partials/transaction_edit.html",
        txn_id=txn_id,
        categories=categories,
        current_category=current_category,
    )


@bp.route("/transactions/<int:txn_id>/row")
def row(txn_id):
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        subq = base_transaction_query().where(txn_table.c.id == txn_id).subquery()
        result = conn.execute(select(subq)).fetchone()
    if not result:
        return "", 404
    return render_template("partials/transaction_row.html", txn=dict(result._mapping))


@bp.route("/transactions/<int:txn_id>/category", methods=["POST"])
def update_category(txn_id):
    category = request.form["category"]
    apply_to_merchant = request.form.get("apply_to_merchant") == "on"

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        if apply_to_merchant:
            row = conn.execute(
                select(txn_table.c.normalized_merchant).where(txn_table.c.id == txn_id)
            ).fetchone()
            if row:
                set_merchant_category(conn, row[0], category, source="manual")
            # Many rows changed — reload the content area preserving URL state
            current_url = request.headers.get("HX-Current-URL", "/transactions")
            return "", 204, {"HX-Redirect": current_url}
        else:
            apply_transaction_correction(conn, txn_id, category=category)

        subq = base_transaction_query().where(txn_table.c.id == txn_id).subquery()
        updated = conn.execute(select(subq)).fetchone()

    if not updated:
        return "", 404

    row_html = render_template(
        "partials/transaction_row.html", txn=dict(updated._mapping)
    )
    oob_delete = f'<tr id="edit-{txn_id}" hx-swap-oob="delete"></tr>'
    return Response(row_html + oob_delete, content_type="text/html")
