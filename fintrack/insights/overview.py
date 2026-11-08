"""Overview: the at-a-glance numbers behind the web Overview page and the
`status` CLI command.

Each card is a key in the returned ``cards`` list plus the data it needs.
Cards whose feature hasn't shipped are simply absent; to add one, gather its
data here, append its key, and add ``web/templates/overview/cards/<key>.html``
(the CLI prints a section per card too).
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy.engine import Connection

from fintrack.insights.cash_flow import get_cash_flow, trailing_months_start
from fintrack.projections import engine

# Cash flow card's average window: Trends' Trailing 12.
CASH_FLOW_MONTHS = 12

# Projection horizon the Overview reports on ("over the next 12 months").
OVERVIEW_MONTHS = 12


def build_overview(
    conn: Connection, snapshot_id: int, today: date | None = None
) -> dict[str, Any]:
    """Current liquid / net worth, where they're projected to be in
    OVERVIEW_MONTHS, and the projection's warnings (lowest liquid month,
    accounts dipping below their minimum, loan payoffs)."""
    today = today or datetime.now().astimezone().date()
    result = engine.project(conn, snapshot_id, months=OVERVIEW_MONTHS, today=today)
    summary = engine.summarize(result)
    return {
        "cards": ["net_worth_liquid", "cash_flow", "projection_warnings"],
        "months": OVERVIEW_MONTHS,
        "has_holdings": bool(result["rows"]),
        "summary": summary,
        "cash_flow": _cash_flow_card(conn, snapshot_id, today),
    }


def _cash_flow_card(conn: Connection, snapshot_id: int, today: date) -> dict[str, Any]:
    """This month's income, spending, and % saved so far, plus the average
    over Trends' Trailing 12 window (completed months with data)."""
    flow = get_cash_flow(
        conn,
        snapshot_id,
        start=trailing_months_start(today, CASH_FLOW_MONTHS),
        end=today,
        today=today,
        with_budget=False,
    )
    current = (today.year, today.month)
    this_month = next(
        (m for m in flow["months"] if (m["year"], m["month"]) == current), None
    )
    return {
        "month_label": f"{today:%B}",
        "this_month": this_month
        or {"income": 0.0, "spending": 0.0, "net": 0.0, "rate": None},
        "has_this_month": this_month is not None,
        "average": flow["average"],
        "avg_month_count": flow["avg_month_count"],
        "months": CASH_FLOW_MONTHS,
    }
