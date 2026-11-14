from datetime import date, datetime
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit

from flask import Blueprint, abort, current_app, g, render_template, request
from sqlalchemy import select
from werkzeug.datastructures import MultiDict

from fintrack.budget import reconcile
from fintrack.budget.repository import get_budget_entries
from fintrack.core.coerce import parse_amount_filter
from fintrack.core.formatting import fmt_account_label
from fintrack.core.models import transactions as txn_table
from fintrack.insights.alerts import alerts_by_transaction
from fintrack.ledger.repository.accounts import list_accounts
from fintrack.ledger.repository.aggregations import base_transaction_query
from fintrack.ledger.repository.alerts import (
    dismiss_category_alert,
    dismiss_transaction_alert,
    undismiss_transaction_alert,
)
from fintrack.ledger.repository.categories import get_category_names
from fintrack.ledger.repository.corrections import apply_transaction_correction
from fintrack.ledger.repository.duplicates import (
    DUPLICATE_STATUSES,
    resolve_duplicate,
)
from fintrack.ledger.repository.merchants import set_merchant_category
from fintrack.ledger.repository.transactions import get_transactions
from web.routes.common import snapshot_scoped
from web.routes.grouped_sheet import (
    NEUTRAL_TONE,
    GroupBy,
    category_bands,
    flat_sheet,
    selected_group,
)

bp = snapshot_scoped(Blueprint("transactions", __name__, url_prefix="/s/<filename>"))

# The Transactions sheet's columns (an unbanded grouped sheet). Row templates
# map cells by key, never by position (partials/transaction_row.html).
TRANSACTIONS_SHEET = flat_sheet(
    "transactions",
    [
        ("date", "Date", False, 1),
        ("merchant", "Merchant", False, 1),
        ("description", "Description", False, 1),
        ("account", "Account", False, 1),
        ("category", "Category", False, 1),
        ("amount", "Amount", True, 1),
        ("notes", "Notes", False, 1),
        ("budget", "Budget", False, 1),
    ],
    {
        "date": "Transaction date from statement",
        "merchant": "Normalized merchant name",
        "description": "Original description from statement",
        "account": "Account this transaction belongs to",
        "category": "Spending category",
        "amount": "Transaction amount (negative = expense)",
        "notes": "Optional notes",
        "budget": "Budget entry this transaction realizes",
    },
)


# "Group by" choices (issue #137); the first (a flat list) is the default.
# Category bands take the category's color (the same hue as on Budget);
# Account and Month bands are neutral. Each band's heading shows its row count
# and the Amount subtotal; the pinned footer still totals every row. Month
# bands (newest first) are for the Year / All time windows.
TRANSACTION_GROUPS = [
    GroupBy("none", "None"),
    GroupBy("category", "Category"),
    GroupBy("account", "Account"),
    GroupBy("month", "Month"),
]


def _band(key: str, label: str, tone: str, rows: list[dict]) -> dict:
    return {
        **TRANSACTIONS_SHEET,
        "key": key,
        "flat": False,
        "label": label,
        "tone": tone,
        "rows": rows,
        "count": len(rows),
        "total": sum((t["amount"] for t in rows), Decimal(0)),
    }


def _transaction_bands(
    txns: list[dict], group: str, categories: list[str], accounts: list[dict]
) -> list[dict]:
    """Split the listed transactions into the bands of `group` (not
    ``none``), keeping each band's rows in list (date) order."""
    if group == "category":
        return [
            _band(b.key, b.label, b.tone, b.rows)
            for b in category_bands(
                txns, lambda t: t.get("category"), categories, "txn-cat"
            )
        ]
    if group == "month":
        by_month: dict[date, list[dict]] = {}
        for t in txns:
            by_month.setdefault(t["date"].replace(day=1), []).append(t)
        return [
            _band(f"txn-month-{m:%Y-%m}", f"{m:%b %Y}", NEUTRAL_TONE, by_month[m])
            for m in sorted(by_month, reverse=True)
        ]
    # account
    by_acct: dict[int | None, list[dict]] = {}
    for t in txns:
        by_acct.setdefault(t.get("account_id"), []).append(t)
    known = [a["id"] for a in accounts]
    order = [a for a in known if a in by_acct] + [a for a in by_acct if a not in known]
    label_of = {a["id"]: fmt_account_label(a) for a in accounts}
    return [
        _band(
            f"txn-acct-{a}",
            label_of.get(a) or by_acct[a][0].get("account_name") or "—",
            NEUTRAL_TONE,
            by_acct[a],
        )
        for a in order
    ]


def _page_args() -> MultiDict:
    """The query of the page an htmx request came from (``HX-Current-URL``):
    its filters, window, grouping and edit mode."""
    query = urlsplit(request.headers.get("HX-Current-URL", "")).query
    return MultiDict(parse_qsl(query, keep_blank_values=True))


def _refresh_table():
    """Re-render the whole table for the page's own filters, window and
    grouping, swapped into the sheet's scroll container (#txn-sheet). The
    container stays put, so the scroll position survives — unlike a full
    reload, which jumps back to the top. For edits that change more than one
    row, or move a row between bands."""
    return (
        render_template(
            "partials/transaction_rows.html", **_sheet_context(_page_args())
        ),
        200,
        {"HX-Retarget": "#txn-sheet", "HX-Reswap": "innerHTML"},
    )


def _regroup_response():
    """When the sheet is grouped by category, an edit that can change a row's
    category (a category correction, or a budget link, which pins the
    category) refreshes the table so the row lands in its new band. Otherwise
    None and the edit swaps just the row."""
    if selected_group(TRANSACTION_GROUPS, _page_args()) != "category":
        return None
    return _refresh_table()


# User-editable overlay fields (transaction_corrections). Raw imported columns
# (date, amount, raw_description, account) are immutable per DESIGN.md.
_TXN_EDITABLE_FIELDS = {"category", "merchant_name", "notes"}
# Cells that open an inline editor: the overlay fields above plus the budget
# link, which saves through /link rather than /update.
_TXN_CELL_EDITORS = _TXN_EDITABLE_FIELDS | {"budget"}


def _load_txn(conn, txn_id):
    # include_duplicates: a row just marked a duplicate re-renders (with Undo)
    # rather than vanishing mid-edit.
    subq = (
        base_transaction_query(include_duplicates=True)
        .where(txn_table.c.id == txn_id)
        .subquery()
    )
    row = conn.execute(select(subq)).fetchone()
    return dict(row._mapping) if row else None


def _budget_choices(conn, snapshot_id):
    """Budget-entry link options (with kind + category) + a ref->label map.

    Returned as a flat pool; the Transactions row leads with the row's own
    kind (an expense → expense entries), the row's category first, and lists
    opposite-kind entries last as refunds/clawbacks (issues #53, #70). The
    label map backs the read-only Budget cell."""
    entries = get_budget_entries(conn, snapshot_id)
    options = [
        {
            "value": e["_db_id"],
            "label": e.get("description", ""),
            "kind": e.get("kind", "expense"),
            "category": e.get("category"),
        }
        for e in entries
    ]
    labels = {e["_db_id"]: e.get("description", "") for e in entries}
    return options, labels


def _today():
    return datetime.now().astimezone().date()


def _txn_alerts(conn, snapshot_id):
    """The open spending alert behind each flagged row (issue #101), for the
    row markers and the Flagged filter. Rows in a category running hot this
    month carry that category's alert."""
    return alerts_by_transaction(conn, snapshot_id, _today())


def _render_row(
    conn, txn_id, *, editing_field=None, edit_mode=True, alert_dismissed=False
):
    """Render one edit-mode transaction row, or a 404 when it doesn't exist.

    Shared by the cell/row/update/link routes, which all swap a single row back
    into the Transactions sheet. ``editing_field`` opens that cell's editor.
    ``alert_dismissed`` shows the just-dismissed alert's Undo."""
    txn = _load_txn(conn, txn_id)
    if not txn:
        return "", 404
    budget_options, budget_labels = _budget_choices(conn, g.snapshot_id)
    kwargs = {}
    if editing_field is not None:
        kwargs = {
            "categories": get_category_names(conn),
            "editing_field": editing_field,
        }
    return render_template(
        "partials/transaction_row.html",
        txn=txn,
        edit_mode=edit_mode,
        budget_options=budget_options,
        budget_labels=budget_labels,
        txn_alerts=_txn_alerts(conn, g.snapshot_id),
        alert_dismissed=alert_dismissed,
        **kwargs,
    )


def _sheet_context(args: MultiDict) -> dict:
    """Everything the Transactions page renders, from its query `args` (the
    request's own, or — for an in-place table refresh — the page URL's)."""
    edit_mode = args.get("edit") == "1"
    today = _today()
    year = args.get("year", today.year, type=int)
    month = args.get("month", today.month, type=int)
    # Account and Category are multi-select: repeated ?account_id=/?category=
    # params combine with IN. A single value (e.g. the Trends drill-down link's
    # ?category=Foo) still works — getlist returns a one-item list.
    selected_categories = [c for c in args.getlist("category") if c]
    selected_accounts = []
    for a in args.getlist("account_id"):
        try:
            selected_accounts.append(int(a))
        except ValueError:
            pass
    # Budget entries (issue #143): the rows linked to any of them — what the
    # Budget sheet's Actual cell opens.
    selected_budget = []
    for b in args.getlist("budget"):
        try:
            selected_budget.append(int(b))
        except ValueError:
            pass
    # One smart filter box (QA item 1): `q` filters by amount when it parses as
    # an amount expression (e.g. "45", ">50", "10-20"), otherwise it searches
    # merchant/description text. Legacy `search`/`amount` params are still
    # honored (e.g. the Merchants "view transactions" link, old bookmarks).
    search = args.get("search")
    amount = args.get("amount")
    q = args.get("q")
    if q is not None and q.strip():
        if parse_amount_filter(q) is not None:
            search, amount = None, q
        else:
            search, amount = q, None
    status = args.get("status")
    all_months_val = args.get("all_months", "")
    all_months = all_months_val == "true"
    this_year = all_months_val == "year"

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        txn_alerts = _txn_alerts(conn, g.snapshot_id)
        # "Flagged" (issue #101) is computed, not stored: the rows with an
        # open spending alert.
        flagged = status == "flagged"
        txns = get_transactions(
            conn,
            year=None if all_months else year,
            month=None if (all_months or this_year) else month,
            categories=selected_categories or None,
            account_ids=selected_accounts or None,
            search=search,
            amount=amount,
            status=None if flagged else status,
            transaction_ids=txn_alerts.keys() if flagged else None,
            budget_entry_refs=selected_budget or None,
            snapshot_id=g.snapshot_id,
        )
        accounts = list_accounts(conn, g.snapshot_id)
        categories = get_category_names(conn)
        budget_options, budget_labels = _budget_choices(conn, g.snapshot_id)

    txn_count = len(txns)
    txn_total = sum((t["amount"] for t in txns), Decimal(0))
    group = selected_group(TRANSACTION_GROUPS, args)
    bands = (
        _transaction_bands(txns, group, categories, accounts)
        if group != "none"
        else None
    )

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

    return {
        "active_tab": "transactions",
        "transactions": txns,
        "accounts": accounts,
        "categories": categories,
        "year": year,
        "month": month,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
        "is_latest": is_latest,
        "is_latest_year": is_latest_year,
        "latest_year": today.year,
        "latest_month": today.month,
        "month_label": month_label,
        "selected_categories": selected_categories,
        "selected_accounts": selected_accounts,
        "selected_budget": selected_budget,
        "q": amount or search or "",
        "selected_status": status,
        "all_months": all_months,
        "this_year": this_year,
        "txn_count": txn_count,
        "txn_total": txn_total,
        "edit_mode": edit_mode,
        "budget_options": budget_options,
        "budget_labels": budget_labels,
        "txn_alerts": txn_alerts,
        "group": group,
        "group_options": TRANSACTION_GROUPS,
        "bands": bands,
    }


@bp.route("/transactions")
def index():
    template = (
        "partials/transactions_content.html"
        if request.headers.get("HX-Request")
        else "transactions.html"
    )
    return render_template(template, **_sheet_context(request.args))


@bp.route("/transactions/<int:txn_id>/cell", methods=["GET"])
def cell_edit(txn_id):
    """Return the transaction row with one overlay field in its inline editor."""
    field = request.args.get("field", "category")
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        editing_field = field if field in _TXN_CELL_EDITORS else None
        return _render_row(conn, txn_id, editing_field=editing_field)


@bp.route("/transactions/<int:txn_id>/row")
def row(txn_id):
    """Display (non-editing) transaction row — used to revert an open editor."""
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        return _render_row(conn, txn_id)


@bp.route("/transactions/<int:txn_id>/update", methods=["POST"])
def update(txn_id):
    """Apply an inline spreadsheet edit to a transaction's overlay fields.

    Edits are written to the transaction_corrections overlay; raw imported
    columns are never modified. Editing the category with the apply-to-merchant
    checkbox instead sets the merchant-wide category and refreshes the table.
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
            # Many rows change at once — refresh the table in place.
            return _refresh_table()

        apply_transaction_correction(conn, txn_id, **{field: value})
        if field == "category" and (regrouped := _regroup_response()):
            return regrouped
        return _render_row(conn, txn_id)


@bp.route("/transactions/<int:txn_id>/link", methods=["POST"])
def link(txn_id):
    """Link (or, with an empty value, unlink) a transaction to a budget entry.

    The link is a corrections-overlay field (budget_entry_ref); raw imported
    columns are untouched. See fintrack.budget.reconcile."""
    ref_raw = request.form.get("budget_entry_ref", "").strip()
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        if ref_raw:
            try:
                reconcile.link_transaction(conn, g.snapshot_id, txn_id, int(ref_raw))
            except (ValueError, reconcile.SnapshotMismatch):
                return "", 422
        else:
            reconcile.unlink_transaction(conn, txn_id)
        if regrouped := _regroup_response():
            return regrouped
        return _render_row(conn, txn_id)


@bp.route("/transactions/<int:txn_id>/duplicate", methods=["POST"])
def duplicate(txn_id):
    """Resolve (or undo) a row's possible-duplicate flag (issue #82). Works in
    view and edit mode; the row re-renders in the mode it was shown in."""
    status = request.form.get("status", "")
    if status not in DUPLICATE_STATUSES:
        abort(400)
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        try:
            resolve_duplicate(conn, txn_id, status, snapshot_id=g.snapshot_id)
        except LookupError:
            abort(404)
        return _render_row(conn, txn_id, edit_mode=request.form.get("edit") == "1")


@bp.route("/transactions/<int:txn_id>/alert", methods=["POST"])
def alert(txn_id):
    """Dismiss (or undo dismissing) a row's spending alert (issue #101). A
    dismissed alert is hidden everywhere: the marker, Flagged, Overview, and
    `fintrack alerts`. Works in view and edit mode, like the duplicate
    actions.

    On a row flagged because its category is running hot, Dismiss hides that
    category's alert for the month, which unflags all its rows at once, so
    the view reloads (keeping its URL) instead of swapping one row."""
    action = request.form.get("action", "")
    if action not in ("dismiss", "undo"):
        abort(400)
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        row_alert = _txn_alerts(conn, g.snapshot_id).get(txn_id)
        try:
            if action == "dismiss" and row_alert and row_alert["kind"] == "running_hot":
                dismiss_category_alert(
                    conn, g.snapshot_id, row_alert["category"], row_alert["date"]
                )
                return _refresh_table()
            if action == "dismiss":
                dismiss_transaction_alert(conn, g.snapshot_id, txn_id)
            else:
                undismiss_transaction_alert(conn, g.snapshot_id, txn_id)
        except LookupError:
            abort(404)
        return _render_row(
            conn,
            txn_id,
            edit_mode=request.form.get("edit") == "1",
            alert_dismissed=action == "dismiss",
        )
