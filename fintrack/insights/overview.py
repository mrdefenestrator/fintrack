"""Overview: the at-a-glance numbers behind the web Overview page and the
`status` CLI command.

Each card is a key in the returned ``cards`` list plus the data it needs.
Cards whose feature hasn't shipped are simply absent; to add one, gather its
data here, append its key, and add ``web/templates/overview/cards/<key>.html``
(the CLI prints a section per card too).
"""

from datetime import date
from typing import Any

from sqlalchemy.engine import Connection

from fintrack.projections import engine

# Projection horizon the Overview reports on ("over the next 12 months").
OVERVIEW_MONTHS = 12


def build_overview(
    conn: Connection, snapshot_id: int, today: date | None = None
) -> dict[str, Any]:
    """Current liquid / net worth, where they're projected to be in
    OVERVIEW_MONTHS, and the projection's warnings (lowest liquid month,
    accounts dipping below their minimum, loan payoffs)."""
    result = engine.project(conn, snapshot_id, months=OVERVIEW_MONTHS, today=today)
    summary = engine.summarize(result)
    return {
        "cards": ["net_worth_liquid", "projection_warnings"],
        "months": OVERVIEW_MONTHS,
        "has_holdings": bool(result["rows"]),
        "summary": summary,
    }
