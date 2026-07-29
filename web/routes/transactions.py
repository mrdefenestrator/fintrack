from datetime import date
from decimal import Decimal

from flask import Blueprint, current_app, g, render_template, request
from sqlalchemy import select

from fintrack.core.coerce import parse_amount_filter
from fintrack.core.models import transactions as txn_table
from fintrack.ledger.repository.accounts import list_accounts
from fintrack.ledger.repository.aggregations import base_transaction_query
from fintrack.ledger.repository.categories import get_category_names
from fintrack.ledger.repository.corrections import apply_transaction_correction
from fintrack.ledger.repository.merchants import set_merchant_category
from fintrack.ledger.repository.transactions import get_transactions
from web.routes.common import snapshot_scoped

bp = snapshot_scoped(Blueprint("transactions", __name__, url_prefix="/s/<filename>"))


# User-editable overlay fields (transaction_corrections). Raw imported columns
# (date, amount, raw_description, account) are immutable per DESIGN.md.
_TXN_EDITABLE_FIELDS = {"category", "merchant_name", "notes"}


def _load_txn(conn, txn_id):
    subq = base_transaction_query().where(txn_table.c.id == txn_id).subquery()
    row = conn.execute(select(subq)).fetchone()
    return dict(row._mapping) if row else None


@bp.route("/transactions")
def index():
    edit_mode = request.args.get("edit") == "1"
    today = date.today()
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)
    # Account and Category are multi-select: repeated ?account_id=/?category=
    # params combine with IN. A single value (e.g. the Trends drill-down link's
    # ?category=Foo) still works — getlist returns a one-item list.
    selected_categories = [c for c in request.args.getlist("category") if c]
    selected_accounts = []
    for a in request.args.getlist("account_id"):
        try:
            selected_accounts.append(int(a))
        except ValueError:
            pass
    # One smart filter box (QA item 1): `q` filters by amount when it parses as
    # an amount expression (e.g. "45", ">50", "10-20"), otherwise it searches
    # merchant/description text. Legacy `search`/`amount` params are still
    # honored (e.g. the Merchants "view transactions" link, old bookmarks).
    search = request.args.get("search")
    amount = request.args.get("amount")
    q = request.args.get("q")
    if q is not None and q.strip():
        if parse_amount_filter(q) is not None:
            search, amount = None, q
        else:
            search, amount = q, None
    status = request.args.get("status")
    all_months_val = request.args.get("all_months", "")
    all_months = all_months_val == "true"
    this_year = all_months_val == "year"

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        txns = get_transactions(
            conn,
            year=None if all_months else year,
            month=None if (all_months or this_year) else month,
            categories=selected_categories or None,
            account_ids=selected_accounts or None,
            search=search,
            amount=amount,
            status=status,
            snapshot_id=g.snapshot_id,
        )
        accounts = list_accounts(conn, g.snapshot_id)
        categories = get_category_names(conn)

    txn_count = len(txns)
    txn_total = sum((t["amount"] for t in txns), Decimal("0"))

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    # "Latest" = the current calendar month; mirror the Trends pager (Latest
    # button + disabled next arrow once you're at/after it) so both time pagers
    # behave identically.
    is_latest = (year, month) >= (today.year, today.month)
    is_latest_year = year >= today.year
    # Human-readable month label ("Jul 2026"), matching the Trends window label
    # (%b %Y) so the two pagers read the same.
    month_label = f"{date(year, month, 1):%b %Y}"

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
        is_latest=is_latest,
        is_latest_year=is_latest_year,
        latest_year=today.year,
        latest_month=today.month,
        month_label=month_label,
        selected_categories=selected_categories,
        selected_accounts=selected_accounts,
        q=amount or search or "",
        selected_status=status,
        all_months=all_months,
        this_year=this_year,
        txn_count=txn_count,
        txn_total=txn_total,
        edit_mode=edit_mode,
    )


@bp.route("/transactions/<int:txn_id>/cell", methods=["GET"])
def cell_edit(txn_id):
    """Return the transaction row with one overlay field in its inline editor."""
    field = request.args.get("field", "category")
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        categories = get_category_names(conn)
        txn = _load_txn(conn, txn_id)
    if not txn:
        return "", 404
    kwargs = {"txn": txn, "categories": categories, "edit_mode": True}
    if field in _TXN_EDITABLE_FIELDS:
        kwargs["editing_field"] = field
    return render_template("partials/transaction_row.html", **kwargs)


@bp.route("/transactions/<int:txn_id>/row")
def row(txn_id):
    """Display (non-editing) transaction row — used to revert an open editor."""
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        txn = _load_txn(conn, txn_id)
    if not txn:
        return "", 404
    return render_template("partials/transaction_row.html", txn=txn, edit_mode=True)


@bp.route("/transactions/<int:txn_id>/update", methods=["POST"])
def update(txn_id):
    """Apply an inline spreadsheet edit to a transaction's overlay fields.

    Edits are written to the transaction_corrections overlay; raw imported
    columns are never modified. Editing the category with the apply-to-merchant
    checkbox instead sets the merchant-wide category and reloads the list.
    """
    field = request.form.get("field", "").strip()
    value = request.form.get("value", "").strip()
    apply_to_merchant = request.form.get("apply_to_merchant") == "on"

    if field not in _TXN_EDITABLE_FIELDS:
        return "", 422

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        if field == "category" and apply_to_merchant:
            merchant_row = conn.execute(
                select(txn_table.c.normalized_merchant).where(txn_table.c.id == txn_id)
            ).fetchone()
            if merchant_row is None:
                return "", 404
            set_merchant_category(conn, merchant_row[0], value, source="manual")
            # Many rows change at once — reload the content area, preserving
            # the current URL's filter/sort/month state.
            current_url = request.headers.get(
                "HX-Current-URL", f"/s/{g.filename}/transactions"
            )
            return "", 204, {"HX-Redirect": current_url}

        apply_transaction_correction(conn, txn_id, **{field: value})
        txn = _load_txn(conn, txn_id)

    if not txn:
        return "", 404
    return render_template(
        "partials/transaction_row.html", txn=txn, edit_mode=True
    )
