from datetime import date
from collections import defaultdict

from flask import Blueprint, current_app, redirect, render_template, request, url_for

from spending.repository.aggregations import get_monthly_totals_range

bp = Blueprint("trends", __name__)


def _period_range(period: str, today: date) -> tuple[date, date]:
    if period == "quarterly":
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        return date(today.year, quarter_start_month, 1), today
    elif period == "ytd":
        return date(today.year, 1, 1), today
    elif period == "trailing12":
        start_year = today.year - 1
        start_month = today.month + 1
        if start_month > 12:
            start_month -= 12
            start_year += 1
        return date(start_year, start_month, 1), today
    elif period == "annual":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    else:
        return date(today.year, 1, 1), today


@bp.route("/")
def root():
    return redirect(url_for("trends.index"))


@bp.route("/trends")
def index():
    today = date.today()
    period = request.args.get("period", "trailing12")
    start, end = _period_range(period, today)

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        monthly_data = get_monthly_totals_range(conn, start_date=start, end_date=end)

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

        trends.append(
            {
                "category": cat,
                "total": data["total"],
                "monthly_avg": monthly_avg,
                "monthly_values": monthly_values,
                "monthly_heat": heat,
                "pct_change": pct_change,
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
    )


@bp.route("/trends/detail")
def detail():
    today = date.today()
    period = request.args.get("period", "ytd")
    category = request.args.get("category", "")
    start, end = _period_range(period, today)

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
