"""Load finances data from SQLite database."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def load_finances_from_db(conn, snapshot_id: int) -> dict[str, Any]:
    """Load finances data from DB into a plain dict (same shape as the old YAML structure)."""
    from fintrack.accounts.repository import get_accounts
    from fintrack.budget.repository import get_budget_entries
    from fintrack.networth.repository import get_asset_entries

    raw_accounts = get_accounts(conn, snapshot_id)
    raw_budget = get_budget_entries(conn, snapshot_id)
    raw_assets = get_asset_entries(conn, snapshot_id)

    # Strip internal _db_id keys before returning
    budget = [{k: v for k, v in e.items() if k != "_db_id"} for e in raw_budget]
    assets = [{k: v for k, v in e.items() if k != "_db_id"} for e in raw_assets]

    # Fetch cached (and possibly refresh) external prices for non-USD units.
    # rate_meta carries each cached price's fetched_at so the UI can show how
    # fresh it is (and flag stale ones next to the on-demand refresh control).
    rates: dict[str, Any] = {}
    rate_meta: dict[str, Any] = {}
    units = {e["unit"] for e in assets if e.get("unit") and e["unit"] != "USD"}
    if units:
        try:
            from fintrack.networth.prices import get_price_meta, get_rates

            rates = get_rates(conn, units)
            rate_meta = get_price_meta(conn, units)
        except Exception:
            logger.warning(
                "Price lookup failed; using cached/fallback values", exc_info=True
            )

    return {
        "accounts": list(raw_accounts),
        "budget": budget,
        "assets": assets,
        "rates": rates,
        "rate_meta": rate_meta,
    }
