"""Load finances data from SQLite database."""

from typing import Any, Dict


def load_finances_from_db(conn, snapshot_id: int) -> Dict[str, Any]:
    """Load finances data from DB into a plain dict (same shape as the old YAML structure)."""
    from finances.repository.accounts import get_accounts
    from finances.repository.assets import get_asset_entries
    from finances.repository.budget import get_budget_entries

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
