"""Overview: the at-a-glance numbers behind the web Overview page and the
`status` CLI command.

Each card is a key in the returned ``cards`` list plus the data it needs.
Cards whose feature hasn't shipped are simply absent; to add one, gather its
data here, append its key, and add ``web/templates/overview/cards/<key>.html``
(the CLI prints a section per card too).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.engine import Connection

from fintrack.budget import recurring
from fintrack.insights import debt, stale
from fintrack.insights.alerts import get_alerts
from fintrack.insights.cash_flow import get_cash_flow, trailing_months_start
from fintrack.ledger.alerts import load_alert_config
from fintrack.projections import engine, timeline

# Cash flow card's average window: Trends' Trailing 12.
CASH_FLOW_MONTHS = 12

# Spending alerts card: lines shown before "+N more".
SPENDING_ALERT_LINES = 5

# Projection horizon the Overview reports on ("over the next 12 months").
OVERVIEW_MONTHS = 12

# How far back the Net worth / Liquid card looks: the change since the end of
# the month this many months before the current one (actual balances).
LOOKBACK_MONTHS = 12


def needs_attention(
    conn: Connection, snapshot_id: int, today: date | None = None
) -> list[dict[str, Any]]:
    """The Needs attention card's items, in order: one ``{source, text}`` per
    finding. Each feature that reports here (recurring charges, #100; stale
    data, #102) appends its own items under its own ``source``."""
    today = today or datetime.now().astimezone().date()
    items: list[dict[str, Any]] = []
    for charge in recurring.attention_items(
        recurring.find_recurring(conn, snapshot_id, today=today)
    ):
        items.append({"source": "recurring", "text": recurring.summary_line(charge)})
    for item in stale.find_stale(conn, snapshot_id, today):
        items.append({"source": "stale", "text": stale.summary_line(item)})
    return items


def build_overview(
    conn: Connection, snapshot_id: int, today: date | None = None
) -> dict[str, Any]:
    """Current liquid / net worth, how they changed over the past
    LOOKBACK_MONTHS and where they're projected to be in OVERVIEW_MONTHS,
    this month's cash flow, debt and card usage, what needs attention,
    spending alerts, and the projection's warnings (lowest liquid month,
    accounts dipping below their minimum, loan payoffs)."""
    today = today or datetime.now().astimezone().date()
    # Projections' timeline over the past year and the next: actual month-ends
    # back to LOOKBACK_MONTHS ago, then the OVERVIEW_MONTHS projection.
    tl = timeline.build_timeline(
        conn,
        snapshot_id,
        start=timeline.shift_month((today.year, today.month), -LOOKBACK_MONTHS),
        months=LOOKBACK_MONTHS + OVERVIEW_MONTHS,
        today=today,
    )
    result = tl["projection"]
    summary = engine.summarize(result)
    return {
        "cards": [
            "net_worth_liquid",
            "cash_flow",
            "debt_cards",
            "needs_attention",
            "spending_alerts",
            "projection_warnings",
        ],
        "months": OVERVIEW_MONTHS,
        "has_holdings": bool(result["rows"]),
        "summary": summary,
        "past": _past_change(tl),
        "cash_flow": _cash_flow_card(conn, snapshot_id, today),
        "debt_cards": debt.debt_and_cards(conn, snapshot_id, today),
        "attention": needs_attention(conn, snapshot_id, today=today),
        "spending_alerts": _spending_alerts_card(conn, snapshot_id, today),
    }


def _past_change(tl: dict[str, Any]) -> dict[str, Any]:
    """Liquid and net worth now against the timeline's first month-end (the
    first column of the linked Projections window), like for like: the change
    is summed over the holdings that had a balance recorded by then, so one
    added since doesn't count as growth. ``partial`` when some didn't (as in
    Projections' Changes view); None when none did."""
    first = tl["months"][0]
    out: dict[str, Any] = {"label": first["label"], "from": first["key"]}
    for key, flag in (("liquid", "in_liquid"), ("net_worth", "in_net_worth")):
        counted = [r for r in tl["rows"] if r[flag]]
        known = [r for r in counted if r["cells"][0]["value"] is not None]
        if not known:
            out[key] = None
            continue
        out[key] = {
            "then": tl[key][0]["value"],
            "change": sum(
                (r["start"] - r["cells"][0]["value"] for r in known), Decimal(0)
            ),
            "partial": len(known) < len(counted),
        }
    return out


def _spending_alerts_card(
    conn: Connection, snapshot_id: int, today: date
) -> dict[str, Any]:
    """Open spending alerts (issue #101), one line each. Their own card, so the
    whole card can open the one view behind them (Transactions > Flagged);
    other findings (#100, #102) go on Needs attention."""
    config = load_alert_config()
    alerts = get_alerts(conn, snapshot_id, today, config=config)
    items: list[dict[str, Any]] = [dict(a) for a in alerts]
    return {
        "window_days": config.window_days,
        "items": items[:SPENDING_ALERT_LINES],
        "count": len(items),
        "more": max(0, len(items) - SPENDING_ALERT_LINES),
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
