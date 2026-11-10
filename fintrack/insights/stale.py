"""Stale data (issue #102): holdings whose balance hasn't been updated in a
while, and accounts with no recent statement import.

Thresholds per holding group live in ``configs/insights.yaml``. The checks
feed Overview > Needs attention (and so `status`); Holdings colors the As Of
cell with ``staleness()``.
"""

from datetime import date
from typing import Literal, TypedDict

from sqlalchemy.engine import Connection

from fintrack.accounts.repository import get_accounts
from fintrack.core.types import group_for_account_type, group_for_kind
from fintrack.insights.settings import InsightsConfig, load_insights_config
from fintrack.ledger.repository import last_import_dates
from fintrack.networth.repository import get_asset_entries

Staleness = Literal["stale", "very_stale"] | None


class StaleItem(TypedDict):
    kind: Literal["balance", "import"]
    holding_id: int
    name: str
    group: str
    days: int


def staleness(
    as_of: date | str | None, group: str, today: date, config: InsightsConfig
) -> Staleness:
    """How stale a holding's As Of date is for its group, or None if fresh
    (or unknown)."""
    if not as_of:
        return None
    try:
        day = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    except ValueError:
        return None
    age = (today - day).days
    if age > config.very_stale_days[group]:
        return "very_stale"
    if age > config.stale_days[group]:
        return "stale"
    return None


def find_stale(
    conn: Connection,
    snapshot_id: int,
    today: date,
    config: InsightsConfig | None = None,
) -> list[StaleItem]:
    """Stale balances, oldest first, then accounts overdue for an import.

    A holding with a stale balance isn't also reported for its imports: one
    line per holding is enough, and updating it fixes both."""
    config = config or load_insights_config()
    holdings = [
        (a["id"], a["name"], group_for_account_type(a.get("type")), a.get("asOfDate"))
        for a in get_accounts(conn, snapshot_id)
    ] + [
        (e["id"], e["name"], group_for_kind(e["kind"]), e.get("asOfDate"))
        for e in get_asset_entries(conn, snapshot_id)
    ]
    balance: list[StaleItem] = []
    for holding_id, name, group, as_of in holdings:
        if staleness(as_of, group, today, config):
            days = (today - date.fromisoformat(as_of)).days
            balance.append(
                StaleItem(
                    kind="balance",
                    holding_id=holding_id,
                    name=name,
                    group=group,
                    days=days,
                )
            )
    flagged = {item["holding_id"] for item in balance}
    names = {h[0]: (h[1], h[2]) for h in holdings}
    overdue: list[StaleItem] = []
    for account_id, last in last_import_dates(conn, snapshot_id).items():
        days = (today - last).days
        if account_id in flagged or account_id not in names:
            continue
        if days > config.import_stale_days:
            name, group = names[account_id]
            overdue.append(
                StaleItem(
                    kind="import",
                    holding_id=account_id,
                    name=name,
                    group=group,
                    days=days,
                )
            )
    return sorted(balance, key=_by_age) + sorted(overdue, key=_by_age)


def _by_age(item: StaleItem) -> tuple[int, str]:
    return (-item["days"], item["name"])


def summary_line(item: StaleItem) -> str:
    """One terse line for the Overview card and `status`."""
    if item["kind"] == "balance":
        return f"{item['name']}: last updated {item['days']} days ago"
    return f"{item['name']}: no statement imported in {item['days']} days"
