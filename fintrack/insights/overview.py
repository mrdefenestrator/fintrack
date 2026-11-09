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

from fintrack.insights.alerts import get_alerts
from fintrack.insights.cash_flow import get_cash_flow, trailing_months_start
from fintrack.ledger.alerts import load_alert_config
from fintrack.projections import engine

# Cash flow card's average window: Trends' Trailing 12.
CASH_FLOW_MONTHS = 12

# Needs attention card: lines shown before "+N more".
NEEDS_ATTENTION_LINES = 5

# Projection horizon the Overview reports on ("over the next 12 months").
OVERVIEW_MONTHS = 12


def build_overview(
    conn: Connection, snapshot_id: int, today: date | None = None
) -> dict[str, Any]:
    """Current liquid / net worth, where they're projected to be in
    OVERVIEW_MONTHS, this month's cash flow, what needs attention (spending
    alerts), and the projection's warnings (lowest liquid month, accounts
    dipping below their minimum, loan payoffs)."""
    today = today or datetime.now().astimezone().date()
    result = engine.project(conn, snapshot_id, months=OVERVIEW_MONTHS, today=today)
    summary = engine.summarize(result)
    return {
        "cards": [
            "net_worth_liquid",
            "cash_flow",
            "needs_attention",
            "projection_warnings",
        ],
        "months": OVERVIEW_MONTHS,
        "has_holdings": bool(result["rows"]),
        "summary": summary,
        "cash_flow": _cash_flow_card(conn, snapshot_id, today),
        "needs_attention": _needs_attention_card(conn, snapshot_id, today),
    }


def _needs_attention_card(
    conn: Connection, snapshot_id: int, today: date
) -> dict[str, Any]:
    """Things to look at, one line each. Every item is a dict with at least
    ``kind`` and ``message``; other checks (missed charges, stale balances)
    append their own items here. Today: open spending alerts (issue #101)."""
    config = load_alert_config()
    alerts = get_alerts(conn, snapshot_id, today, config=config)
    items: list[dict[str, Any]] = [dict(a) for a in alerts]
    return {
        "window_days": config.window_days,
        "items": items[:NEEDS_ATTENTION_LINES],
        "count": len(items),
        "more": max(0, len(items) - NEEDS_ATTENTION_LINES),
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
