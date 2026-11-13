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

"Group by" (issue #137, ``?group=``) can instead band the sheet by kind or
recurrence (neutral bands; rows keep their category color, no drag-reorder) or
show it unbanded (``none``, reordered as one list); see BUDGET_GROUPS.

Rows are addressed by their position in the full, sort-ordered budget list
(`index`), so edit/delete URLs are unaffected by grouping. Drag-reorder is per
band: the band's local permutation is mapped onto the band's own slots in the
full ordering (grouped_sheet.remap_group_order).

Budget history (issue #136): the sheet shows the plan for one month (``ym``,
default this month; a month pager above it). Only entries in that month's plan
are shown, each as planned then. Every URL the sheet renders for its own
endpoints carries ``ym`` (``_add_viewed_month`` below), so each request — htmx
attribute or cell-nav.js's htmx.ajax alike — lands in that month, and edits
apply from that month until the entry's next change. In edit mode a row can
be ended after the viewed month (or resumed, in its last month). A past amount
is corrected by paging to the month its change started and editing it there;
the change dot's tooltip lists the entry's whole history.
"""

from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from flask import (
    Blueprint,
    abort,
    current_app,
    g,
    render_template,
    request,
    url_for,
)

from fintrack.budget import reconcile, recurring
from fintrack.budget import repository as repo_budget
from fintrack.budget.recurrence import as_month, entry_active
from fintrack.core import filters, tables
from fintrack.core.formatting import fmt_money, fmt_recurrence_display
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
from .grouped_sheet import (
    NEUTRAL_TONE,
    GroupBy,
    band_key,
    category_order,
    category_tones,
    flat_sheet,
    group_columns,
    parse_order,
    remap_group_order,
    selected_group,
    unique_keys,
)

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
    "remaining": "Prorated amount remaining this month (— for a past month)",
    "monthly": "Scheduled in the month shown (an annual bill due in another month is $0)",
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
# Budget vs Actual's Recurring view (issue #100, read-only apart from the
# edit-mode actions): one row per recurring merchant series.
RECURRING_SHEET = flat_sheet(
    "recurring",
    [
        ("merchant", "Merchant", False, 1),
        ("category", "Category", False, 1),
        ("amount", "Typical", True, 1),
        ("every", "Every", False, 1),
        ("count", "N", True, 1),
        ("last", "Last", False, 1),
        ("next", "Next", False, 1),
        ("budget", "Budget", False, 1),
        ("change", "Price change", False, 1),
    ],
    {
        "merchant": "Merchant (expenses negative, income positive)",
        "category": "The merchant's category",
        "amount": "Typical charge at the current price",
        "every": "How often it charges",
        "count": "Charges in the last 13 months",
        "last": "Most recent charge",
        "next": "When the next charge is expected",
        "budget": "In budget (linked) / not linked to its entry / covered by a variable category line / not in budget",
        "change": "A recent change from the usual amount",
    },
)
# Recurring view Status filter, in display order. "price_change" is a flag,
# not a budget status; it matches any series with a recent price change.
RECURRING_STATUSES = {
    "not_budgeted": "Not in budget",
    "price_change": "Price change",
    "unlinked": "Not linked",
    "covered": "By category",
    "linked": "In budget",
}
RECURRING_EVERY = {
    "weekly": "Week",
    "biweekly": "2 weeks",
    "monthly": "Month",
    "annual": "Year",
}

# The heading label spans Category..When; the Remaining / This month / Per month /
# Annual subtotals sit under their columns.
_LABEL_SPAN = 5
_TOTAL_POS = [5, 6, 7, 8]
_UNCATEGORIZED = "Uncategorized"

# "Group by" choices (issue #137); the first is the default. Category bands
# are colored per category and drag-reorderable; Kind and Recurrence bands are
# neutral (each row keeps its category's color on its rail) and locked, since
# the stored order is per category. None is a flat sheet: one body, reordered
# as the whole list.
BUDGET_GROUPS = [
    GroupBy("category", "Category"),
    GroupBy("kind", "Kind"),
    GroupBy("recurrence", "Recurrence"),
    GroupBy("none", "None"),
]
# Recurrence bands run from most to least frequent; one-time entries close.
_RECURRENCE_BAND_ORDER = [
    "weekly",
    "biweekly",
    "monthly",
    "quarterly",
    "semiannual",
    "annual",
    "one_time",
]
_BUDGET_FLAT = flat_sheet("budget", _COLS, _TOOLTIPS)


def _band_key(category: str) -> str:
    """A stable DOM/URL-safe key for a category band (per-band sort state)."""
    return band_key("cat", category) if category else "uncategorized"


def _subtotals(rows: list[dict], past: bool) -> list[str]:
    """Remaining / This month / Per month / Annual sums over `rows`, formatted;
    a past month has no Remaining ("—")."""
    return [
        "—"
        if past and j == 0
        else fmt_money(sum((r["figures"][j] for r in rows), Decimal(0)))
        for j in range(len(_TOTAL_POS))
    ]


def _band(
    key: str,
    label: str,
    tone: str,
    rows: list[dict],
    add: list[dict],
    past: bool,
    **extra,
) -> dict:
    """One band: heading label/tone, rows, subtotals, and its add buttons
    (``{kind, category, recurrence}`` posts for ``budget.add``)."""
    return {
        "key": key,
        "label": label,
        "tone": tone,
        "rows": rows,
        "totals": _subtotals(rows, past),
        "add": add,
        "reorder": None,
        **_COLUMN_META,
        **extra,
    }


def _add_kinds(rows: list[dict], **vals) -> list[dict]:
    """Add buttons for both kinds, the band's majority kind first."""
    incomes = sum(r["kind"] == "income" for r in rows)
    kinds = ["income", "expense"] if incomes * 2 > len(rows) else ["expense", "income"]
    return [{"kind": k, **vals} for k in kinds]


def _group_bands(
    rows: list[dict], group: str, all_categories: list[str], past: bool
) -> list:
    """Split the shown rows into the bands of `group` (not ``none``)."""
    if group == "category":
        by: dict[str, list[dict]] = {}
        for r in rows:
            by.setdefault(r["category"], []).append(r)
        order = [c for c in all_categories if c in by] + ([""] if "" in by else [])
        tones = category_tones(all_categories)
        return [
            _band(
                key,
                c or _UNCATEGORIZED,
                tones.get(c, NEUTRAL_TONE),
                by[c],
                _add_kinds(by[c], category=c),
                past,
                reorder={"category": c},
            )
            for c, key in zip(order, unique_keys(_band_key(c) for c in order))
        ]
    if group == "kind":
        return [
            _band(
                f"kind-{k}",
                k.capitalize(),
                NEUTRAL_TONE,
                band_rows,
                [{"kind": k}],
                past,
            )
            for k in BUDGET_KINDS
            if (band_rows := [r for r in rows if r["kind"] == k])
        ]
    # recurrence
    used = {r["recurrence"] for r in rows}
    order = [r for r in _RECURRENCE_BAND_ORDER if r in used] + sorted(
        used - set(_RECURRENCE_BAND_ORDER)
    )
    bands = []
    for rec in order:
        band_rows = [r for r in rows if r["recurrence"] == rec]
        bands.append(
            _band(
                band_key("rec", rec),
                fmt_recurrence_display(rec),
                NEUTRAL_TONE,
                band_rows,
                _add_kinds(band_rows, recurrence=rec),
                past,
            )
        )
    return bands


_REMAINING = 5  # Remaining's position in a row's cells (Kind dropped)


# The Budget sheet's own request endpoints: their URLs carry the viewed month.
_SHEET_ENDPOINTS = {
    "budget.cell_edit",
    "budget.update",
    "budget.when_update",
    "budget.add",
    "budget.reorder",
    "budget.delete_btn",
    "budget.delete_confirm",
    "budget.delete",
    "budget.end",
    "budget.resume",
}


@budget_bp.url_defaults
def _add_viewed_month(endpoint: str, values: dict) -> None:
    """While a request is about a month of the sheet, every sheet URL built
    (url_for) carries it as ``ym`` — so edits made there apply to that month."""
    if endpoint in _SHEET_ENDPOINTS and "ym" in g:
        values.setdefault("ym", g.ym)


def _viewed_month() -> date:
    """The month the sheet shows (``ym=YYYY-MM`` in the query or form; this
    month when absent or malformed). Remembered for ``_add_viewed_month``."""
    raw = request.values.get("ym", "")
    try:
        year, month = (int(p) for p in raw.split("-"))
        viewed = date(year, month, 1)
    except ValueError:
        viewed = repo_budget.this_month()
    g.ym = _ym(viewed)
    return viewed


def _ym(d: date) -> str:
    return f"{d:%Y-%m}"


def _planned(budget: list[dict], viewed: date) -> list[int]:
    """Indices of the entries in the viewed month's plan."""
    return [
        i for i, e in enumerate(budget) if entry_active(e, viewed.year, viewed.month)
    ]


def _month_before(month: date) -> date:
    return date(month.year - (month.month == 1), (month.month - 2) % 12 + 1, 1)


def _history_lines(entry: dict) -> list[str]:
    """The entry's versions, oldest first, one line each: the months it
    covers, then its amount and schedule ("Until Jul 2026: ($45.00) Monthly,
    2nd")."""
    sign = -1 if entry.get("kind") == "expense" else 1
    versions = repo_budget.entry_versions(entry)
    end = as_month(entry.get("endMonth"))
    lines = []
    for i, v in enumerate(versions):
        start = as_month(v["from"])
        nxt = as_month(versions[i + 1]["from"]) if i + 1 < len(versions) else None
        last = _month_before(nxt) if nxt else end
        if start and last:
            span = (
                f"{start:%b %Y}" if start == last else f"{start:%b %Y} – {last:%b %Y}"
            )
        elif start:
            span = f"From {start:%b %Y}"
        elif last:
            span = f"Until {last:%b %Y}"
        else:
            span = "Every month"
        when = tables._expected_display(
            {k: val for k, val in v.items() if val is not None}
        )
        schedule = fmt_recurrence_display(v.get("recurrence") or "-")
        if when not in ("-", ""):
            schedule += f", {when}"
        lines.append(f"{span}: {fmt_money(sign * (v.get('amount') or 0))} {schedule}")
    return lines


def _marks(entry: dict, viewed: date) -> dict:
    """What changes in the viewed month (issue #136), for the row's markers:
    the amount/schedule (its tooltip lists the whole history), the start, the
    end."""
    viewed_iso = viewed.isoformat()
    marks: dict = {}
    versions = entry.get("_versions") or []
    if any(v["from"] == viewed_iso for v in versions[1:]):
        marks["changed"] = "\n".join(
            [f"Changed in {viewed:%b %Y}", *_history_lines(entry)]
        )
    if entry.get("startMonth") == viewed_iso:
        marks["starts"] = f"Starts in {viewed:%b %Y}"
    if entry.get("endMonth") == viewed_iso:
        marks["ends"] = f"Ends after {viewed:%b %Y}"
    return marks


def _sheet_ctx(
    budget: list[dict],
    shown: list[int],
    categories: list[str],
    ctx: dict,
    edit_mode: bool,
    filters_active: bool = False,
    viewed: date | None = None,
    group: str = "category",
) -> dict:
    """Template context for the budget table: the bands of `group` (or, for
    ``none``, one flat body) over the `shown` entries (indices into `budget`),
    the Net footer, and the column metadata. Figures are for the ``viewed``
    month (default: this month): Remaining is the rest of this month, the full
    month for a later one, and "—" for a past one."""
    current = date(ctx["year"], ctx["month"], 1)
    viewed = viewed or current
    past = viewed < current
    day = ctx["day"] if viewed == current else 0
    # Every category in band order; colors index into this full list (not just
    # the shown bands), so a category keeps its color while filters hide its
    # neighbours.
    all_categories = category_order(categories, (e.get("category") for e in budget))
    tones = category_tones(all_categories)
    rows = []
    for i in shown:
        e = budget[i]
        cells, figures = tables.budget_entry_row(
            e,
            viewed.year,
            viewed.month,
            day,
            account_display_by_id=ctx["account_display_by_id"],
        )
        cells = cells[1:]  # drop Kind
        if past:
            cells[_REMAINING] = "—"
            figures = (Decimal(0), *figures[1:])
        category = e.get("category") or ""
        rows.append(
            {
                "index": i,
                "kind": e.get("kind", "income"),
                "category": category,
                "recurrence": e.get("recurrence") or "monthly",
                # Off the category grouping a row keeps its category's color on
                # its own rail (the band is neutral).
                "tone": None
                if group == "category"
                else tones.get(category, NEUTRAL_TONE),
                "cells": cells,
                "figures": figures,
                "marks": _marks(e, viewed),
                "ends_here": e.get("endMonth") == viewed.isoformat(),
            }
        )

    if group == "none":
        flat = {
            **_BUDGET_FLAT,
            "rows": rows,
            "add": _add_kinds(rows) if rows else [{"kind": "expense"}],
            "reorder": {"all": 1},
        }
        groups = [flat] if rows else []
    else:
        flat = None
        groups = _group_bands(rows, group, all_categories, past)

    net = [""] * _NCOLS
    net[0] = "Net"
    for pos, total in zip(_TOTAL_POS, _subtotals(rows, past)):
        net[pos] = total

    return {
        "groups": groups,
        "flat": flat,
        "group": group,
        "group_options": BUDGET_GROUPS,
        "footer_rows": [net] if rows else [],
        "filters_active": filters_active,
        "max_cols": _NCOLS,
        "label_span": _LABEL_SPAN,
        "total_pos": _TOTAL_POS,
        "edit_mode": edit_mode,
        "categories": categories,
        "recurrence_options": RECURRENCE_OPTIONS,
        "account_display_by_id": ctx["account_display_by_id"],
        "viewed_ym": _ym(viewed),
        "viewed_label": f"{viewed:%b %Y}",
    }


def _category_names() -> list[str]:
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        return get_category_names(conn)


def _render_tbody(
    snapshot_id: int,
    filename: str,
    edit_mode=True,
    **editing,
):
    """Re-render the whole grouped table body (colgroup + every band + footer)
    for an edit swap (target #budget-table), for the viewed month. Shows every
    entry in that month's plan: an edit swap doesn't carry the page's
    filters."""
    viewed = _viewed_month()
    ctx = get_common_context(snapshot_id, filename, edit_mode, budget_as_of=viewed)
    budget = ctx["budget"]
    sheet = _sheet_ctx(
        budget,
        _planned(budget, viewed),
        _category_names(),
        ctx,
        edit_mode,
        viewed=viewed,
        group=selected_group(BUDGET_GROUPS),
    )
    return render_template(
        "partials/budget_tbody.html", filename=filename, **sheet, **editing
    )


def _month_urls(filename: str, viewed: date, current: date) -> dict:
    """The Budget sheet's month pager: earlier / later / now links that keep
    the page's other query parameters (filters, edit mode)."""
    others = [(k, v) for k, v in request.args.items(multi=True) if k != "ym"]
    base = url_for("budget.budget_view", filename=filename)

    def url(month: date) -> str:
        params = others + ([("ym", _ym(month))] if month != current else [])
        return f"{base}?{urlencode(params)}" if params else base

    prev = date(viewed.year - (viewed.month == 1), (viewed.month - 2) % 12 + 1, 1)
    nxt = date(viewed.year + (viewed.month == 12), viewed.month % 12 + 1, 1)
    return {
        "month_label": f"{viewed:%b %Y}",
        "prev_url": url(prev),
        "next_url": url(nxt),
        "now_url": url(current),
        "is_current": viewed == current,
        "is_past": viewed < current,
        "actual_url": url_for(
            "budget.actual_view",
            filename=filename,
            year=viewed.year,
            month=viewed.month,
        ),
    }


@budget_bp.route("/<filename>/budget")
def budget_view(filename: str):
    snapshot_id = validate_snapshot(filename)
    edit_mode = request.args.get("edit") == "1"
    viewed = _viewed_month()
    ctx = get_common_context(snapshot_id, filename, edit_mode, budget_as_of=viewed)
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
    planned = _planned(full_budget, viewed)
    filtered = filters.apply_budget_filters(
        [full_budget[i] for i in planned],
        include_kinds=include_kinds or None,
        include_categories=include_categories or None,
        include_recurrence=include_recurrence or None,
    )
    # Filters return the same entry dicts; map them back to their positions in
    # the full list (by identity — two identical entries must stay distinct).
    kept = {id(e) for e in filtered}
    shown = [i for i in planned if id(full_budget[i]) in kept]
    categories = _category_names()
    include_kinds_set = {k.lower() for k in include_kinds}
    ctx["include_kinds"] = [k for k in BUDGET_KINDS if k in include_kinds_set]
    ctx["include_categories"] = [c for c in categories if c in set(include_categories)]
    ctx["include_recurrence"] = [
        r for r in RECURRENCE_OPTIONS if r in set(include_recurrence)
    ]
    ctx["budget_kinds"] = BUDGET_KINDS
    current = date(ctx["year"], ctx["month"], 1)
    ctx.update(_month_urls(filename, viewed, current))
    ctx.update(
        _sheet_ctx(
            full_budget,
            shown,
            categories,
            ctx,
            edit_mode,
            filters_active=len(shown) < len(planned),
            viewed=viewed,
            group=selected_group(BUDGET_GROUPS),
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


def _recurring_action(filename: str, action):
    """Run a Recurring-view action on the posted merchant series, then
    re-render the view for the query string the buttons post to."""
    snapshot_id = validate_snapshot(filename)
    merchant = request.form.get("merchant", "")
    kind = request.form.get("kind", "expense")
    if not merchant or kind not in BUDGET_KINDS:
        return "", 422
    with current_app.config["engine"].connect() as conn:
        try:
            action(conn, snapshot_id, merchant, kind)
        except ValueError:  # NotRecurring, SnapshotMismatch, bad entry_ref
            return "", 422
    return _render_actual(filename, snapshot_id)


@budget_bp.route("/<filename>/budget/actual/recurring/add", methods=["POST"])
def recurring_add(filename: str):
    """Add a recurring series to the budget and link its transactions."""
    return _recurring_action(
        filename, lambda c, s, m, k: recurring.add_to_budget(c, s, m, k)
    )


@budget_bp.route("/<filename>/budget/actual/recurring/link", methods=["POST"])
def recurring_link(filename: str):
    """Link a series to the existing entry that looks like it."""
    try:
        entry_ref = int(request.form["budget_entry_ref"])
    except (KeyError, ValueError):
        return "", 422
    return _recurring_action(
        filename,
        lambda c, s, m, k: recurring.link_to_entry(c, s, m, entry_ref, k),
    )


@budget_bp.route("/<filename>/budget/actual/recurring/dismiss", methods=["POST"])
def recurring_dismiss(filename: str):
    """Mark a merchant "not recurring"."""
    return _recurring_action(filename, lambda c, s, m, k: recurring.dismiss(c, s, m))


def _render_recurring(filename: str, snapshot_id: int, ctx: dict):
    """Budget vs Actual's Recurring view (``?view=recurring``, issue #100):
    every recurring merchant series of the last 13 months and how it sits in
    the budget, with Add to budget / Link / Not recurring in edit mode."""
    with current_app.config["engine"].connect() as conn:
        charges = recurring.find_recurring(conn, snapshot_id)
    include_kinds = [k for k in BUDGET_KINDS if k in request.args.getlist("kind")]
    include_statuses = [
        s for s in RECURRING_STATUSES if s in request.args.getlist("status")
    ]

    def _matches(c) -> bool:
        if include_kinds and c.kind not in include_kinds:
            return False
        if not include_statuses:
            return True
        return c.status in include_statuses or (
            "price_change" in include_statuses and c.price_changed
        )

    shown = [c for c in charges if _matches(c)]
    # A rough monthly figure for the footer: each series at its cadence.
    per_month = sum(
        (c.amount * Decimal(str(30.44 / c.cadence.days)) for c in shown),
        Decimal(0),
    ).quantize(Decimal("0.01"))
    ctx.update(
        view="recurring",
        recurring=shown,
        recurring_total=len(charges),
        not_budgeted_count=sum(c.status == "not_budgeted" for c in charges),
        price_change_count=sum(c.price_changed for c in charges),
        per_month=per_month,
        recurring_every=RECURRING_EVERY,
        recurring_statuses=RECURRING_STATUSES,
        budget_kinds=BUDGET_KINDS,
        include_kinds=include_kinds,
        include_statuses=include_statuses,
        self_query=request.query_string.decode(),
    )
    template = (
        "partials/budget_recurring_content.html"
        if request.headers.get("HX-Request")
        else "budget_actual.html"
    )
    return render_template(template, **ctx)


def _render_actual(filename: str, snapshot_id: int):
    edit_mode = request.args.get("edit") == "1"
    ctx = get_common_context(snapshot_id, filename, edit_mode)
    ctx["active_tab"] = "budget_actual"
    if request.args.get("view") == "recurring":
        return _render_recurring(filename, snapshot_id, ctx)
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
        view="month",
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
    entry = budget[index]
    return render_template(
        "partials/budget_delete_icon.html",
        filename=filename,
        kind=entry.get("kind", "income"),
        index=index,
        edit_mode=True,
        row={"ends_here": entry.get("endMonth") == _viewed_month().isoformat()},
        # Kind / Recurrence bands don't drag-reorder (see BUDGET_GROUPS).
        reorderable=selected_group(BUDGET_GROUPS) in ("category", "none"),
    )


@budget_bp.route("/<filename>/budget/delete-confirm/<int:index>")
def delete_confirm(filename: str, index: int):
    snapshot_id = validate_snapshot(filename)
    _viewed_month()  # the confirm's own URLs keep the month
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
    rows in place. ``?all=1`` (the ungrouped sheet) permutes the whole list."""
    snapshot_id = validate_snapshot(filename)
    category = request.args.get("category", "")
    whole_list = request.args.get("all") == "1"
    viewed = _viewed_month()
    local_order = parse_order(request.form.get("order", ""))
    if not local_order:
        return "", 422
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        budget = load_finances_from_db(conn, snapshot_id).get("budget") or []
        # The band shows only the viewed month's plan; entries outside it
        # keep their slots.
        full = remap_group_order(
            budget,
            lambda e: (
                (whole_list or (e.get("category") or "") == category)
                and entry_active(e, viewed.year, viewed.month)
            ),
            local_order,
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
    # A new entry is in the plan from the month it was added in (issue #136).
    entry = {
        "kind": kind,
        "description": "New entry",
        "amount": 0.0,
        "recurrence": "monthly",
        "startMonth": _viewed_month().isoformat(),
    }
    # Added from a recurrence band: give the stub that band's recurrence.
    recurrence = request.form.get("recurrence", "").strip()
    if recurrence in RECURRENCE_OPTIONS:
        entry["recurrence"] = recurrence
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
        data = load_finances_from_db(conn, snapshot_id, budget_as_of=_viewed_month())
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

    viewed = _viewed_month()
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        data = load_finances_from_db(conn, snapshot_id, budget_as_of=viewed)
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
            repo_budget.update_budget_entry(
                conn, snapshot_id, index, {field: value}, effective=viewed
            )
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
                conn,
                snapshot_id,
                index,
                updates,
                delete_keys,
                effective=_viewed_month(),
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


# ---------------------------------------------------------------------------
# Budget history (issue #136): end / resume
# ---------------------------------------------------------------------------


def _end_action(filename: str, last_month: date | None):
    """End entry ``index`` after ``last_month`` (None resumes it), then
    re-render the sheet for the viewed month (422, unchanged, on bad input)."""
    snapshot_id = validate_snapshot(filename)
    index = request.view_args["index"]
    try:
        with current_app.config["engine"].connect() as conn:
            repo_budget.end_budget_entry(conn, snapshot_id, index, last_month)
    except ValueError:  # bad index, or an end before the entry starts
        return _render_tbody(snapshot_id, filename), 422
    return _render_tbody(snapshot_id, filename)


@budget_bp.route("/<filename>/budget/end/<int:index>", methods=["POST"])
def end(filename: str, index: int):
    """Stop the entry after the viewed month; it stays in earlier months."""
    return _end_action(filename, _viewed_month())


@budget_bp.route("/<filename>/budget/resume/<int:index>", methods=["POST"])
def resume(filename: str, index: int):
    """Undo an end: the entry is open-ended again."""
    return _end_action(filename, None)
