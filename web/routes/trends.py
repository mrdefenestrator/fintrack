import re
from calendar import monthrange
from datetime import date
from collections import defaultdict

from flask import Blueprint, current_app, g, render_template, request

from fintrack.budget.repository import budgeted_monthly_by_category, get_budget_entries
from fintrack.ledger.repository.aggregations import get_monthly_totals_range
from web.routes.common import snapshot_scoped

bp = snapshot_scoped(Blueprint("trends", __name__, url_prefix="/s/<filename>"))

_END_PARAM_RE = re.compile(r"^(\d{4})-(\d{2})$")


# Paging stride per period, in months: each "prev/next" click moves the
# window by one whole page rather than a single month (QA: trends paging).
# Quarterly steps a quarter, YTD steps a calendar year, trailing-12 steps a
# year so the 12-month window slides back/forward intact.
_PERIOD_STRIDE = {"quarterly": 3, "ytd": 12, "trailing12": 12}


def _period_stride(period: str) -> int:
    return _PERIOD_STRIDE.get(period, 12)


def _period_range(period: str, anchor_end: date, is_latest: bool) -> tuple[date, date]:
    """The [start, end] date span for a period window anchored at `anchor_end`.

    The *latest* window is period-to-date (ends at `anchor_end`, i.e. today):
    the current partial quarter or the current year so far. A paged-back
    window is the *full* period containing the anchor month — the full quarter
    or the full calendar year — so stepping back lands on complete periods.
    Trailing-12 is a fixed 12-month window ending at the anchor either way.
    """
    if period == "quarterly":
        quarter_start_month = ((anchor_end.month - 1) // 3) * 3 + 1
        start = date(anchor_end.year, quarter_start_month, 1)
        if is_latest:
            return start, anchor_end
        quarter_end_month = quarter_start_month + 2
        _, last_day = monthrange(anchor_end.year, quarter_end_month)
        return start, date(anchor_end.year, quarter_end_month, last_day)
    elif period == "ytd":
        start = date(anchor_end.year, 1, 1)
        if is_latest:
            return start, anchor_end
        return start, date(anchor_end.year, 12, 31)
    elif period == "trailing12":
        start_year = anchor_end.year - 1
        start_month = anchor_end.month + 1
        if start_month > 12:
            start_month -= 12
            start_year += 1
        return date(start_year, start_month, 1), anchor_end
    else:
        start = date(anchor_end.year, 1, 1)
        return (
            (start, anchor_end) if is_latest else (start, date(anchor_end.year, 12, 31))
        )


def _parse_end_param(value: str | None) -> tuple[int, int] | None:
    """Parse an `end=YYYY-MM` query param. Returns None if missing/malformed
    or the month is out of range — callers fall back to the latest window."""
    if not value:
        return None
    m = _END_PARAM_RE.match(value)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12):
        return None
    return year, month


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Shift a (year, month) pair by `delta` months."""
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def _resolve_window_end(
    end_param: str | None, today: date
) -> tuple[date, int, int, bool]:
    """Resolve the effective end-of-window date for the trends page.

    `end_param` is the raw `end=YYYY-MM` query string value. Malformed values
    and months at-or-after the current month both fall back to "latest"
    (today, so the current partial month behaves exactly as before paging
    existed). Past months resolve to the last calendar day of that month, so
    a paged-back window always covers a full month.

    Returns (effective_end_date, anchor_year, anchor_month, is_latest).
    """
    parsed = _parse_end_param(end_param)
    current_ym = (today.year, today.month)
    if parsed is None or parsed >= current_ym:
        return today, today.year, today.month, True
    year, month = parsed
    _, last_day = monthrange(year, month)
    return date(year, month, last_day), year, month, False


@bp.route("/trends")
def index():
    today = date.today()
    period = request.args.get("period", "trailing12")
    anchor_end, anchor_year, anchor_month, is_latest = _resolve_window_end(
        request.args.get("end"), today
    )
    start, end = _period_range(period, anchor_end, is_latest)

    stride = _period_stride(period)
    prev_year, prev_month = _shift_month(anchor_year, anchor_month, -stride)
    next_year, next_month = _shift_month(anchor_year, anchor_month, stride)
    prev_end = f"{prev_year:04d}-{prev_month:02d}"
    next_end = f"{next_year:04d}-{next_month:02d}"
    anchor_end_param = f"{anchor_year:04d}-{anchor_month:02d}"
    end_qs = "" if is_latest else f"&end={anchor_end_param}"
    if (start.year, start.month) == (end.year, end.month):
        window_label = f"{start:%b %Y}"
    else:
        window_label = f"{start:%b %Y} – {end:%b %Y}"

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        monthly_data = get_monthly_totals_range(
            conn, start_date=start, end_date=end, snapshot_id=g.snapshot_id
        )
        budget = get_budget_entries(conn, g.snapshot_id)

    budgeted, budget_kinds = budgeted_monthly_by_category(budget)

    by_category: dict = defaultdict(lambda: {"total": 0, "months": defaultdict(float)})
    all_months: set[tuple[int, int]] = set()
    for row in monthly_data:
        key = (row["year"], row["month"])
        all_months.add(key)
        by_category[row["category"]]["total"] += float(row["total"])
        by_category[row["category"]]["months"][key] = float(row["total"])

    sorted_months = sorted(all_months)
    num_months = max((end.year - start.year) * 12 + end.month - start.month + 1, 1)
    month_labels = [date(y, m, 1).strftime("%b") for y, m in sorted_months]

    trends = []
    for cat, data in sorted(by_category.items(), key=lambda x: x[1]["total"]):
        monthly_values = [data["months"].get(m, 0) for m in sorted_months]
        abs_values = [abs(v) for v in monthly_values]
        max_abs = max(abs_values) if any(v > 0 for v in abs_values) else 1

        heat = [v / max_abs for v in abs_values]

        monthly_avg = data["total"] / num_months

        # Change: last month in period vs period average (empty months count as 0)
        last_val = monthly_values[-1] if monthly_values else None
        if last_val is not None and monthly_avg != 0:
            pct_change = (abs(last_val) - abs(monthly_avg)) / abs(monthly_avg) * 100
        else:
            pct_change = None

        bgt = budgeted.get(cat)
        kind = budget_kinds.get(cat)
        if bgt is not None:
            bgt_display = float(-bgt if kind == "expense" else bgt)
            budget_delta = monthly_avg - bgt_display
        else:
            bgt_display = None
            budget_delta = None

        trends.append(
            {
                "category": cat,
                "total": data["total"],
                "monthly_avg": monthly_avg,
                "monthly_values": monthly_values,
                "monthly_heat": heat,
                "pct_change": pct_change,
                "budget_monthly": bgt_display,
                "budget_delta": budget_delta,
            }
        )

    excluded = {"Transfer"}
    trends_main = [t for t in trends if t["category"] not in excluded]
    trends_excluded = [t for t in trends if t["category"] in excluded]
    grand_total = sum(t["total"] for t in trends_main)

    monthly_footer: dict[tuple[int, int], float] = defaultdict(float)
    for row in monthly_data:
        if row["category"] not in excluded:
            monthly_footer[(row["year"], row["month"])] += float(row["total"])
    monthly_footer_values = [monthly_footer.get(m, 0) for m in sorted_months]

    footer_avg = grand_total / num_months
    last_val_footer = monthly_footer_values[-1] if monthly_footer_values else None
    if last_val_footer is not None and footer_avg != 0:
        footer_pct_change: float | None = (
            (abs(last_val_footer) - abs(footer_avg)) / abs(footer_avg) * 100
        )
    else:
        footer_pct_change = None

    budgeted_rows = [t for t in trends_main if t["budget_monthly"] is not None]
    if budgeted_rows:
        footer_budget: float | None = sum(t["budget_monthly"] for t in budgeted_rows)
        footer_budget_delta: float | None = footer_avg - footer_budget
    else:
        footer_budget = None
        footer_budget_delta = None

    template = (
        "partials/trends_table.html"
        if request.headers.get("HX-Request")
        else "trends.html"
    )
    return render_template(
        template,
        active_tab="trends",
        trends=trends_main,
        trends_excluded=trends_excluded,
        grand_total=grand_total,
        period=period,
        month_labels=month_labels,
        sorted_months=sorted_months,
        monthly_footer_values=monthly_footer_values,
        num_months=num_months,
        footer_pct_change=footer_pct_change,
        footer_budget=footer_budget,
        footer_budget_delta=footer_budget_delta,
        prev_end=prev_end,
        next_end=next_end,
        anchor_end=anchor_end_param,
        end_qs=end_qs,
        is_latest=is_latest,
        window_label=window_label,
    )


@bp.route("/trends/detail")
def detail():
    today = date.today()
    period = request.args.get("period", "ytd")
    category = request.args.get("category", "")
    anchor_end, _, _, is_latest = _resolve_window_end(request.args.get("end"), today)
    start, end = _period_range(period, anchor_end, is_latest)

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        monthly_data = get_monthly_totals_range(conn, start_date=start, end_date=end)

    all_months: set[tuple[int, int]] = set()
    cat_months: dict[tuple[int, int], float] = {}
    for row in monthly_data:
        key = (row["year"], row["month"])
        all_months.add(key)
        if row["category"] == category:
            cat_months[key] = float(row["total"])

    sorted_months = sorted(all_months)
    month_labels = [date(y, m, 1).strftime("%b '%y") for y, m in sorted_months]
    values = [cat_months.get(m, 0) for m in sorted_months]
    abs_values = [abs(v) for v in values]
    max_abs = max(abs_values) if any(v > 0 for v in abs_values) else 1

    return render_template(
        "partials/trends_detail.html",
        category=category,
        month_labels=month_labels,
        values=values,
        max_abs=max_abs,
    )
