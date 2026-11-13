from urllib.parse import parse_qsl, urlsplit

from flask import Blueprint, current_app, g, render_template, request
from werkzeug.datastructures import MultiDict

from fintrack.ledger.repository.categories import get_category_names
from fintrack.ledger.repository.merchants import (
    get_merchant_with_stats_by_id,
    list_merchants_with_stats,
    set_merchant_category,
)
from web.routes.common import snapshot_scoped
from web.routes.grouped_sheet import (
    NEUTRAL_TONE,
    GroupBy,
    category_bands,
    flat_sheet,
    selected_group,
)

bp = snapshot_scoped(Blueprint("merchants", __name__, url_prefix="/s/<filename>"))

# The Merchants sheet's columns (an unbanded grouped sheet); rows map cells by
# key (partials/merchant_row.html).
MERCHANTS_SHEET = flat_sheet(
    "merchants",
    [
        ("merchant", "Merchant", False, 1),
        ("category", "Category", False, 1),
        ("source", "Source", False, 1),
        ("txn_count", "Transactions", True, 1),
        ("last_seen", "Last Seen", False, 1),
    ],
    {
        "merchant": "Normalized merchant name",
        "category": "Assigned spending category",
        "source": "How the category was assigned (auto, manual, correction)",
        "txn_count": "Total number of transactions",
        "last_seen": "Date of most recent transaction",
    },
)


# "Group by" choices (issue #137); the first (a flat list) is the default.
# Category bands take the category's color (as on Budget / Transactions);
# Source bands are neutral. A band heading shows its merchant count and the
# Transactions subtotal.
MERCHANT_GROUPS = [
    GroupBy("none", "None"),
    GroupBy("category", "Category"),
    GroupBy("source", "Source"),
]
_SOURCE_LABELS = {"api": "Auto", "manual": "Manual"}


def _merchant_bands(merchants: list[dict], group: str, categories: list[str]):
    """Split the listed merchants into the bands of `group` (not ``none``),
    keeping each band's rows in list (name) order."""
    if group == "category":
        bands = [
            (b.key, b.label, b.tone, b.rows)
            for b in category_bands(
                merchants, lambda m: m.get("category"), categories, "merch-cat"
            )
        ]
    else:  # source
        by: dict[str, list[dict]] = {}
        for m in merchants:
            by.setdefault(m.get("source") or "", []).append(m)
        order = [s for s in _SOURCE_LABELS if s in by] + sorted(
            set(by) - set(_SOURCE_LABELS)
        )
        bands = [
            (
                f"merch-src-{s or 'none'}",
                _SOURCE_LABELS.get(s, s or "—"),
                NEUTRAL_TONE,
                by[s],
            )
            for s in order
        ]
    return [
        {
            **MERCHANTS_SHEET,
            "key": key,
            "flat": False,
            "label": label,
            "tone": tone,
            "rows": rows,
            "count": len(rows),
            "txn_total": sum(m["txn_count"] for m in rows),
        }
        for key, label, tone, rows in bands
    ]


# Fields on a merchant row that participate in the spreadsheet inline editor.
# merchant_name is the cache key (used to join transactions) and is shown as a
# link, not editable; source / count / last-seen are computed display columns.
_MERCHANT_EDITABLE_FIELDS = {"category"}


def _sheet_context(args: MultiDict) -> dict:
    """Everything the Merchants page renders, from its query `args` (the
    request's own, or — for an in-place table refresh — the page URL's)."""
    edit_mode = args.get("edit") == "1"
    search = args.get("search", "")
    filter_category = args.get("category", "")
    filter_source = args.get("source", "")

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        merchants = list_merchants_with_stats(conn, snapshot_id=g.snapshot_id)
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
    # client-side in the web UI (web/static/js/group-sort.js).
    merchants = sorted(merchants, key=lambda m: (m["merchant_name"] or "").lower())
    group = selected_group(MERCHANT_GROUPS, args)
    return {
        "active_tab": "merchants",
        "edit_mode": edit_mode,
        "merchants": merchants,
        "categories": categories,
        "search": search,
        "selected_category": filter_category,
        "selected_source": filter_source,
        "merchant_count": len(merchants),
        "total_txn_count": sum(m["txn_count"] for m in merchants),
        "group": group,
        "group_options": MERCHANT_GROUPS,
        "bands": _merchant_bands(merchants, group, categories)
        if group != "none"
        else None,
    }


@bp.route("/merchants")
def index():
    template = (
        "partials/merchants_content.html"
        if request.headers.get("HX-Request")
        else "merchants.html"
    )
    return render_template(template, **_sheet_context(request.args))


@bp.route("/merchants/<int:merchant_id>/cell", methods=["GET"])
def cell_edit(merchant_id):
    """Return the merchant row with one field switched to its inline editor."""
    field = request.args.get("field", "category")
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        merchant = get_merchant_with_stats_by_id(
            conn, merchant_id, snapshot_id=g.snapshot_id
        )
        categories = get_category_names(conn)
    if not merchant:
        return "", 404
    if field not in _MERCHANT_EDITABLE_FIELDS:
        return render_template(
            "partials/merchant_row.html",
            m=merchant,
            edit_mode=True,
        )
    return render_template(
        "partials/merchant_row.html",
        m=merchant,
        categories=categories,
        editing_field=field,
        edit_mode=True,
    )


@bp.route("/merchants/<int:merchant_id>/row")
def row(merchant_id):
    """Display (non-editing) merchant row — used to revert an open editor."""
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        merchant = get_merchant_with_stats_by_id(
            conn, merchant_id, snapshot_id=g.snapshot_id
        )
    if not merchant:
        return "", 404
    return render_template(
        "partials/merchant_row.html",
        m=merchant,
        edit_mode=True,
    )


@bp.route("/merchants/<int:merchant_id>/category", methods=["POST"])
def update_category(merchant_id):
    category = request.form.get("value", request.form.get("category", "")).strip()

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        merchant = get_merchant_with_stats_by_id(
            conn, merchant_id, snapshot_id=g.snapshot_id
        )
        if not merchant:
            return "", 404
        if category:
            set_merchant_category(
                conn, merchant["merchant_name"], category, source="manual"
            )
            merchant = get_merchant_with_stats_by_id(
                conn, merchant_id, snapshot_id=g.snapshot_id
            )
    # Grouped by category, the row moves bands: re-render the whole table into
    # the sheet's scroll container (it stays put, so the scroll survives).
    page = MultiDict(
        parse_qsl(
            urlsplit(request.headers.get("HX-Current-URL", "")).query,
            keep_blank_values=True,
        )
    )
    if category and selected_group(MERCHANT_GROUPS, page) == "category":
        return (
            render_template("partials/merchant_rows.html", **_sheet_context(page)),
            200,
            {"HX-Retarget": "#merchants-sheet", "HX-Reswap": "innerHTML"},
        )
    return render_template(
        "partials/merchant_row.html",
        m=merchant,
        edit_mode=True,
    )
