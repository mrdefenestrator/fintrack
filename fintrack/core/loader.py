"""Load finances data from SQLite database."""

from typing import Any, Dict


def load_finances_from_db(conn, snapshot_id: int) -> Dict[str, Any]:
    """Load finances data from DB into a plain dict (same shape as the old YAML structure)."""
    from fintrack.accounts.repository import get_accounts
    from fintrack.networth.repository import get_asset_entries
    from fintrack.budget.repository import get_budget_entries

    raw_accounts = get_accounts(conn, snapshot_id)
    raw_budget = get_budget_entries(conn, snapshot_id)
    raw_assets = get_asset_entries(conn, snapshot_id)

    # Strip internal _db_id keys before returning
    budget = [{k: v for k, v in e.items() if k != "_db_id"} for e in raw_budget]
    assets = [{k: v for k, v in e.items() if k != "_db_id"} for e in raw_assets]

    return {
        "accounts": list(raw_accounts),
        "budget": budget,
        "assets": assets,
    }
