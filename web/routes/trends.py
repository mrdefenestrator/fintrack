import re
import statistics
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime

from flask import Blueprint, current_app, g, render_template, request

from fintrack.budget.recurrence import month_sequence
from fintrack.budget.repository import budgeted_monthly_by_category, get_budget_entries
from fintrack.insights.cash_flow import compute_cash_flow
from fintrack.insights.year_review import year_review
from fintrack.ledger.repository.aggregations import get_monthly_totals_range
from fintrack.ledger.trends import averaging_months, monthly_average, pct_change
from web.routes.common import snapshot_scoped

bp = snapshot_scoped(Blueprint("trends", __name__, url_prefix="/s/<filename>"))

_END_PARAM_RE = re.compile(r"^(\d{4})-(\d{2})$")


# Paging stride per period, in months: each "prev/next" click moves the
# window by one whole page rather than a single month (QA: trends paging).
# Quarterly steps a quarter, Year steps a calendar year, trailing-12 steps a
# year so the 12-month window slides back/forward intact.
_PERIOD_STRIDE = {"quarterly": 3, "year": 12, "trailing12": 12}


def _normalize_period(period: str | None, default: str) -> str:
    """The period param, with the old "ytd" (now "year", #102) still accepted."""
    period = period or default
    return "year" if period == "ytd" else period


def _period_stride(period: str) -> int:
    return _PERIOD_STRIDE.get(period, 12)


def _period_range(period: str, anchor_end: date, is_latest: bool) -> tuple[date, date]:
    """The [start, end] date span for a period window anchored at `anchor_end`.

    The *latest* window is period-to-date (ends at `anchor_end`, i.e. today):
    the current partial quarter or the current year so far (Year). A paged-back
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
    elif period == "year":
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
    today = datetime.now().astimezone().date()
    period = _normalize_period(request.args.get("period"), "trailing12")
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
        # Year in review (#102): this year against the same span last year,
        # top merchants, and net worth start -> end.
        review = (
            year_review(conn, g.snapshot_id, anchor_year, today)
            if period == "year"
            else None
        )
    prev_totals = {c["category"]: c for c in review["categories"]} if review else {}

    # Budget/mo is each window month's own plan, averaged (issue #136), so a
    # past window is measured against the budget it had.
    window_months = month_sequence(
        start.year,
        start.month,
        (end.year - start.year) * 12 + end.month - start.month + 1,
    )
    budgeted, budget_kinds = budgeted_monthly_by_category(budget, window_months)

    by_category: dict = defaultdict(lambda: {"total": 0, "months": defaultdict(float)})
    all_months: set[tuple[int, int]] = set()
    for row in monthly_data:
        key = (row["year"], row["month"])
        all_months.add(key)
        by_category[row["category"]]["total"] += float(row["total"])
        by_category[row["category"]]["months"][key] = float(row["total"])

    sorted_months = sorted(all_months)
    month_labels = [date(y, m, 1).strftime("%b") for y, m in sorted_months]

    # The month still in progress (only when the window ends today): its
    # figures are partial, so it's marked, never tinted as unusual, and left
    # out of Avg/mo and Change.
    highlight = request.args.get("highlight") != "off"
    current_key = (today.year, today.month)
    in_progress = (
        sorted_months.index(current_key)
        if is_latest and current_key in sorted_months
        else None
    )
    # Avg/mo divides by the completed months that have data, not every
    # calendar month in the window (#103); Change compares the last completed
    # month against that average.
    avg_months = averaging_months(
        sorted_months, current_key if in_progress is not None else None
    )
    last_done = avg_months[-1] if avg_months else None

    trends = []
    for cat, data in sorted(by_category.items(), key=lambda x: x[1]["total"]):
        monthly_values = [data["months"].get(m, 0) for m in sorted_months]
        tints = (
            _unusual_tints(monthly_values, in_progress, is_income=data["total"] > 0)
            if highlight
            else [_PLAIN] * len(monthly_values)
        )
        monthly_avg = monthly_average(data["months"], avg_months)
        last_val = data["months"].get(last_done, 0) if last_done else None

        bgt = budgeted.get(cat)
        kind = budget_kinds.get(cat)
        if bgt is not None:
            bgt_display = float(-bgt if kind == "expense" else bgt)
            budget_delta = (
                monthly_avg - bgt_display if monthly_avg is not None else None
            )
        else:
            bgt_display = None
            budget_delta = None

        trends.append(
            {
                "category": cat,
                "total": data["total"],
                "monthly_avg": monthly_avg,
                "monthly_values": monthly_values,
                "monthly_tints": tints,
                "pct_change": pct_change(last_val, monthly_avg),
                "budget_monthly": bgt_display,
                "budget_delta": budget_delta,
                "delta_words": _delta_words(budget_delta, kind or "expense"),
                "prev_total": prev_totals.get(cat, {}).get("prev_total"),
                "year_change": prev_totals.get(cat, {}).get("change_pct"),
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

    footer_avg = monthly_average(monthly_footer, avg_months)
    footer_pct_change = pct_change(
        monthly_footer.get(last_done, 0) if last_done else None, footer_avg
    )

    budgeted_rows = [t for t in trends_main if t["budget_monthly"] is not None]
    footer_budget = (
        sum(t["budget_monthly"] for t in budgeted_rows) if budgeted_rows else None
    )
    footer_budget_delta = (
        footer_avg - footer_budget
        if footer_avg is not None and footer_budget is not None
        else None
    )

    # Income / Spending / Savings rate / Budgeted net footer rows (#99).
    cash_flow = compute_cash_flow(
        monthly_data,
        sorted_months,
        current_key if in_progress is not None else None,
        budget,
    )

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
        footer_avg=footer_avg,
        footer_delta_words=_delta_words(footer_budget_delta, "net"),
        avg_month_count=len(avg_months),
        highlight=highlight,
        highlight_qs="" if highlight else "&highlight=off",
        in_progress=in_progress,
        footer_pct_change=footer_pct_change,
        footer_budget=footer_budget,
        footer_budget_delta=footer_budget_delta,
        cash_flow=cash_flow,
        prev_end=prev_end,
        next_end=next_end,
        anchor_end=anchor_end_param,
        end_qs=end_qs,
        is_latest=is_latest,
        window_label=window_label,
        review=review,
        # Extra columns the Year period adds after the months.
        year_cols=3 if review else 0,
    )


@bp.route("/trends/detail")
def detail():
    today = datetime.now().astimezone().date()
    period = _normalize_period(request.args.get("period"), "year")
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


# "Unusual months" highlight: a month is shaded only when it differs from the
# category's usual (median completed month) by more than this share *and* by
# at least this many dollars (so $36 -> $38 never flags).
_UNUSUAL = 0.15
_UNUSUAL_MIN_DOLLARS = 10.0
_PLAIN = {"cls": "", "title": ""}


def _unusual_tints(
    values: list[float],
    in_progress: int | None,
    *,
    is_income: bool,
) -> list[dict]:
    """Per-month cell style for Trends' unusual-months highlight: {cls, title}.

    `cls` is a CSS class defined in trends_table.html (light and dark shades),
    or "" for a plain cell. Months worse than the category's usual (its median
    completed month) are red, better ones green, in three strengths; more
    spending or less income is worse. Steady bills like rent stay plain, and
    the median keeps one spike from making every normal month look "below
    usual". The month in progress is partial, so it is left plain and out of
    "usual".
    """
    out = []
    done = sorted(abs(v) for i, v in enumerate(values) if i != in_progress)
    usual = statistics.median(done) if done else 0
    for i, v in enumerate(values):
        size = abs(v)
        if i == in_progress or abs(size - usual) < _UNUSUAL_MIN_DOLLARS:
            out.append(_PLAIN)
            continue
        if not usual:
            # Usually nothing here: any amount at all is a one-off.
            out.append(
                {
                    "cls": f"heat-{'better' if is_income else 'worse'}-2",
                    "title": "One-off: usually nothing in this category",
                }
            )
            continue
        dev = (size - usual) / usual
        if abs(dev) <= _UNUSUAL:
            out.append(_PLAIN)
            continue
        worse = (dev < 0) if is_income else (dev > 0)
        level = 1 if abs(dev) < 0.5 else 2 if abs(dev) < 1 else 3
        word = "above" if dev > 0 else "below"
        out.append(
            {
                "cls": f"heat-{'worse' if worse else 'better'}-{level}",
                "title": f"{abs(dev) * 100:.0f}% {word} usual (${usual:,.0f}/mo)",
            }
        )
    return out


def _delta_words(delta: float | None, kind: str) -> dict | None:
    """Wording and tone for Trends' Delta (average minus budget), by row kind
    (#103): spending reads "over"/"under", income and the net footer read
    "short"/"ahead". The tone is whether that's good or bad."""
    if delta is None:
        return None
    if abs(delta) <= 0.5:
        return {"word": "on budget", "tone": "neutral"}
    good = delta > 0  # less spent, more earned, or a bigger net surplus
    if kind == "expense":
        word = "under" if good else "over"
    else:
        word = "ahead" if good else "short"
    return {"word": word, "tone": "good" if good else "bad"}
