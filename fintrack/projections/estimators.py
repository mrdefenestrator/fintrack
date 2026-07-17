"""Unscheduled-spend estimator: what leaves the accounts each month beyond
the scheduled budget entries.

Estimate = trailing N-month average ledger spend per category, minus the
categories already claimed by budget entries (budget_entries.category), so a
budgeted expense isn't counted twice. Opt-in: projections only apply it when
asked.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List

from sqlalchemy import Connection

from fintrack.ledger.repository.aggregations import get_rolling_average

# Internal movements (credit-card payments, account-to-account transfers)
# are not spend; the projection engine models CC autopay explicitly.
EXCLUDED_CATEGORIES = {"Transfer"}


def unscheduled_spend_by_category(
    conn: Connection,
    snapshot_id: int,
    budget: List[Dict[str, Any]],
    *,
    today: date | None = None,
    months_back: int = 3,
) -> Dict[str, Decimal]:
    """Category → average monthly ledger flow (negative = spend) over the
    trailing `months_back` full months, keeping only net-spend categories
    that no budget entry claims."""
    today = today or date.today()
    claimed = {e.get("category") for e in budget if e.get("category")}
    averages = get_rolling_average(
        conn,
        year=today.year,
        month=today.month,
        months_back=months_back,
        snapshot_id=snapshot_id,
    )
    return {
        category: avg
        for category, avg in averages.items()
        if category not in claimed and category not in EXCLUDED_CATEGORIES and avg < 0
    }


def unscheduled_monthly_total(spend_by_category: Dict[str, Decimal]) -> Decimal:
    """Total estimated unscheduled monthly flow (negative = spend)."""
    return sum(spend_by_category.values(), Decimal("0"))
