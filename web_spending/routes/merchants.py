from flask import Blueprint, current_app, render_template, request

from spending.repository.categories import get_category_names
from spending.repository.merchants import (
    get_merchant_with_stats_by_id,
    list_merchants_with_stats,
    set_merchant_category,
)

bp = Blueprint("merchants", __name__)


_MERCHANT_SORT_KEYS = {
    "merchant": lambda m: (m["merchant_name"] or "").lower(),
    "category": lambda m: (m["category"] or "").lower(),
    "source": lambda m: (m["source"] or "").lower(),
    "txn_count": lambda m: m["txn_count"] or 0,
    "last_seen": lambda m: m["last_seen"] or "",
}


@bp.route("/merchants")
def index():
    search = request.args.get("search", "")
    filter_category = request.args.get("category", "")
    filter_source = request.args.get("source", "")
    sort = request.args.get("sort", "")
    sort_dir = request.args.get("dir", "")

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

    if sort in _MERCHANT_SORT_KEYS:
        merchants = sorted(
            merchants, key=_MERCHANT_SORT_KEYS[sort], reverse=(sort_dir == "desc")
        )
    else:
        merchants = sorted(merchants, key=lambda m: (m["merchant_name"] or "").lower())

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
        sort=sort,
        dir=sort_dir,
    )


@bp.route("/merchants/<int:merchant_id>/edit", methods=["GET"])
def edit_form(merchant_id):
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        categories = get_category_names(conn)
        merchant = get_merchant_with_stats_by_id(conn, merchant_id)
    if not merchant:
        return "", 404
    return render_template(
        "partials/merchant_edit.html", merchant=merchant, categories=categories
    )


@bp.route("/merchants/<int:merchant_id>/row")
def row(merchant_id):
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        merchant = get_merchant_with_stats_by_id(conn, merchant_id)
    if not merchant:
        return "", 404
    return render_template("partials/merchant_row.html", m=merchant)


@bp.route("/merchants/<int:merchant_id>/category", methods=["POST"])
def update_category(merchant_id):
    category = request.form["category"]
    merchant_name = request.form["merchant_name"]

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        set_merchant_category(conn, merchant_name, category, source="manual")
        merchant = get_merchant_with_stats_by_id(conn, merchant_id)
    if not merchant:
        return "", 404
    return render_template("partials/merchant_row.html", m=merchant)
