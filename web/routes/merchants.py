from flask import Blueprint, current_app, render_template, request

from web.routes.common import snapshot_scoped

from fintrack.ledger.repository.categories import get_category_names
from fintrack.ledger.repository.merchants import (
    get_merchant_with_stats_by_id,
    list_merchants_with_stats,
    set_merchant_category,
)

bp = snapshot_scoped(Blueprint("merchants", __name__, url_prefix="/s/<filename>"))


# Fields on a merchant row that participate in the spreadsheet inline editor.
# merchant_name is the cache key (used to join transactions) and is shown as a
# link, not editable; source / count / last-seen are computed display columns.
_MERCHANT_EDITABLE_FIELDS = {"category"}


@bp.route("/merchants")
def index():
    search = request.args.get("search", "")
    filter_category = request.args.get("category", "")
    filter_source = request.args.get("source", "")

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        merchants = list_merchants_with_stats(conn)
        categories = get_category_names(conn)

    if search:
        merchants = [
            m for m in merchants if search.upper() in m["merchant_name"].upper()
        ]
    if filter_category:
        merchants = [m for m in merchants if m["category"] == filter_category]
    if filter_source:
        merchants = [m for m in merchants if m["source"] == filter_source]

    # Default (case-insensitive by name) order; column sorting is done
    # client-side in the web UI (web/static/js/sortable.js).
    merchants = sorted(merchants, key=lambda m: (m["merchant_name"] or "").lower())
    merchant_count = len(merchants)
    total_txn_count = sum(m["txn_count"] for m in merchants)

    template = (
        "partials/merchants_content.html"
        if request.headers.get("HX-Request")
        else "merchants.html"
    )
    return render_template(
        template,
        active_tab="merchants",
        merchants=merchants,
        categories=categories,
        search=search,
        selected_category=filter_category,
        selected_source=filter_source,
        merchant_count=merchant_count,
        total_txn_count=total_txn_count,
    )


@bp.route("/merchants/<int:merchant_id>/cell", methods=["GET"])
def cell_edit(merchant_id):
    """Return the merchant row with one field switched to its inline editor."""
    field = request.args.get("field", "category")
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        merchant = get_merchant_with_stats_by_id(conn, merchant_id)
        categories = get_category_names(conn)
    if not merchant:
        return "", 404
    if field not in _MERCHANT_EDITABLE_FIELDS:
        return render_template("partials/merchant_row.html", m=merchant)
    return render_template(
        "partials/merchant_row.html",
        m=merchant,
        categories=categories,
        editing_field=field,
    )


@bp.route("/merchants/<int:merchant_id>/row")
def row(merchant_id):
    """Display (non-editing) merchant row — used to revert an open editor."""
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        merchant = get_merchant_with_stats_by_id(conn, merchant_id)
    if not merchant:
        return "", 404
    return render_template("partials/merchant_row.html", m=merchant)


@bp.route("/merchants/<int:merchant_id>/category", methods=["POST"])
def update_category(merchant_id):
    # `value` is the spreadsheet cell input name; fall back to the legacy
    # `category` field name for compatibility.
    category = request.form.get("value", request.form.get("category", "")).strip()

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        merchant = get_merchant_with_stats_by_id(conn, merchant_id)
        if not merchant:
            return "", 404
        if category:
            set_merchant_category(
                conn, merchant["merchant_name"], category, source="manual"
            )
            merchant = get_merchant_with_stats_by_id(conn, merchant_id)
    return render_template("partials/merchant_row.html", m=merchant)
