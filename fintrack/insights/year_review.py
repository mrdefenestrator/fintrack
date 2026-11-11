"""Year in review (issue #102): one calendar year next to the one before.

Per category, the year's total and last year's over the same span; the top
merchants by spending; income, spending, and savings rate (the shared cash
flow calculation, #99); and net worth at the start and end of the year from
balance history (#98, #107). Feeds Trends' Year period and `report trends
--year`.

A year still in progress is compared with the same dates last year
(Jan 1 – Nov 10 against Jan 1 – Nov 10), so a partial year isn't set
against a whole one.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.engine import Connection

from fintrack.accounts.balance_history import balances_on
from fintrack.accounts.repository import get_accounts
from fintrack.insights.cash_flow import (
    EXCLUDED_CATEGORIES,
    INCOME_CATEGORY,
    compute_cash_flow,
    savings_rate,
)
from fintrack.ledger.repository import get_merchant_totals, get_monthly_totals_range
from fintrack.ledger.trends import pct_change
from fintrack.networth.repository import get_asset_entries

TOP_MERCHANTS = 10


def _same_day_last_year(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # Feb 29
        return date(day.year - 1, 2, 28)


def _totals(
    conn: Connection, snapshot_id: int, start: date, end: date
) -> tuple[dict[str, float], dict[str, Any]]:
    """Per-category totals and the span's cash flow (income, spending, net,
    savings rate) for [start, end]."""
    rows = get_monthly_totals_range(
        conn, start_date=start, end_date=end, snapshot_id=snapshot_id
    )
    by_category: dict[str, float] = defaultdict(float)
    for row in rows:
        by_category[row["category"]] += float(row["total"])
    months = sorted({(r["year"], r["month"]) for r in rows})
    flow = compute_cash_flow(rows, months)["months"]
    income = sum(m["income"] for m in flow)
    spending = sum(m["spending"] for m in flow)
    net = income - spending
    return dict(by_category), {
        "income": income,
        "spending": spending,
        "net": net,
        "rate": savings_rate(net, income),
        "has_data": bool(rows),
    }


def _net_worth(conn: Connection, snapshot_id: int, start: date, end: date) -> dict:
    """Net worth from balance history at the end of the day before `start` and
    on `end`. As on the Holdings footer, a holding with no value by the start
    counts at its end value (no change), and the change is only shown once
    most holdings have a starting value."""
    ids = [
        h["id"]
        for h in [
            *get_accounts(conn, snapshot_id),
            *get_asset_entries(conn, snapshot_id),
        ]
        if h.get("id") is not None
    ]
    opening = balances_on(conn, ids, date.fromordinal(start.toordinal() - 1))
    closing = balances_on(conn, ids, end)
    zero = Decimal(0)
    end_value = sum(closing.values(), zero)
    start_value = sum(
        (opening.get(i, closing.get(i, zero)) for i in closing.keys() | opening.keys()),
        zero,
    )
    return {
        "start": start_value,
        "end": end_value,
        "change": end_value - start_value,
        "covered": len(opening),
        "holdings": len(closing.keys() | opening.keys()),
        "shows_change": len(opening) * 2 > len(closing.keys() | opening.keys()),
    }


def year_review(
    conn: Connection, snapshot_id: int, year: int, today: date
) -> dict[str, Any]:
    """The review for calendar `year`, up to today when it's still running."""
    start = date(year, 1, 1)
    end = min(date(year, 12, 31), today)
    prev_start, prev_end = date(year - 1, 1, 1), _same_day_last_year(end)

    categories, flow = _totals(conn, snapshot_id, start, end)
    prev_categories, prev_flow = _totals(conn, snapshot_id, prev_start, prev_end)

    rows = []
    for cat in sorted(set(categories) | set(prev_categories)):
        total = categories.get(cat, 0.0)
        prev = prev_categories.get(cat, 0.0)
        rows.append(
            {
                "category": cat,
                "total": total,
                "prev_total": prev,
                # Change in size (money in or out), as Trends' Change does.
                "change_pct": pct_change(abs(total), abs(prev)) if prev else None,
                "is_income": cat == INCOME_CATEGORY,
            }
        )
    # Largest outflows first, like Trends; income rises to the bottom.
    rows.sort(key=lambda r: (r["category"] in EXCLUDED_CATEGORIES, r["total"]))

    merchants = [
        {"merchant": m["merchant"], "count": m["count"], "spent": -float(m["total"])}
        for m in get_merchant_totals(
            conn,
            start_date=start,
            end_date=end,
            snapshot_id=snapshot_id,
            exclude_categories=(INCOME_CATEGORY, *EXCLUDED_CATEGORIES),
        )
        if m["total"] < 0
    ][:TOP_MERCHANTS]

    return {
        "year": year,
        "start": start,
        "end": end,
        "partial": end < date(year, 12, 31),
        "prev_year": year - 1,
        "prev_end": prev_end,
        "categories": rows,
        "cash_flow": flow,
        "prev_cash_flow": prev_flow,
        "top_merchants": merchants,
        "net_worth": _net_worth(conn, snapshot_id, start, end),
    }
