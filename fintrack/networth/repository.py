"""Asset/debt repository: CRUD + move for asset and loan holdings.

Serves the unified loan group (which includes loans formerly tracked as
accounts) as `kind="debt"` dicts and the asset group as `kind="asset"`
dicts. The dict API keeps the historical positive amount-owed convention for
a debt's `balance`; the stored loan_details.balance is signed (negative =
owed) like every other balance, and the sign flips at this boundary.

Debts no longer carry unit/quantity (docs/notes-schema-split.md D2): an add
with a quantity folds it into the balance; on existing rows the fields are
gone.
"""

from typing import Any

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from fintrack.core.coerce import to_date
from fintrack.core.holdings import (
    delete_holding,
    get_holding,
    insert_holding,
    load_holdings,
    reorder_merged,
    swap_adjacent,
    update_holding,
)
from fintrack.core.types import ASSET_GROUP_TYPES, AssetEntry

# Order of the merged asset/debt list behind the sort-order index API. Assets
# precede loans to match the old single-table insertion order the index-based
# callers (and their tests) assume; each group is still ordered by its own
# sort_order. (The Holdings page groups rows itself, so this order only fixes
# the opaque index handle, not any on-screen order.)
_ASSET_GROUPS = ("asset", "loan")

_DATE_COLS = ("origination_date", "as_of_date")

_SPINE_FIELD_TO_COL = {
    "name": "name",
    "institution": "institution",
    "type": "type",
    "asOfDate": "as_of_date",
}

_DETAIL_FIELD_TO_COL: dict[str, dict[str, str]] = {
    "asset": {
        "unit": "unit",
        "quantity": "quantity",
        "value": "value",
        "source": "source",
        "annualReturnRate": "annual_return_rate",
        "monthlyContribution": "monthly_contribution",
    },
    "loan": {
        "balance": "balance",
        "interestRate": "interest_rate",
        "originalPrincipal": "original_principal",
        "termMonths": "term_months",
        "originationDate": "origination_date",
        "statement_due_day_of_month": "statement_due_day_of_month",
        "assetRef": "secured_asset_ref",
        "paymentAccountRef": "payment_account_ref",
    },
}


def _negate(value: Any) -> Any:
    return None if value is None else -value


def _row_to_asset_entry(row: dict[str, Any]) -> AssetEntry:
    is_loan = row["group_key"] == "loan"
    entry: AssetEntry = {
        "kind": "debt" if is_loan else "asset",
        "name": row["name"],
    }
    # Asset-only: id for cross-reference (a debt's assetRef points at it).
    if not is_loan:
        entry["id"] = row["id"]
    if is_loan:
        optional_map = {
            "type": "type",
            "institution": "institution",
            "interest_rate": "interestRate",
            "original_principal": "originalPrincipal",
            "term_months": "termMonths",
            "origination_date": "originationDate",
            "statement_due_day_of_month": "statement_due_day_of_month",
            "secured_asset_ref": "assetRef",
            "payment_account_ref": "paymentAccountRef",
            "as_of_date": "asOfDate",
        }
        # Dict API convention: positive amount owed (stored signed).
        if row.get("balance") is not None:
            entry["balance"] = -row["balance"]
    else:
        optional_map = {
            "type": "type",
            "unit": "unit",
            "institution": "institution",
            "value": "value",
            "source": "source",
            "annual_return_rate": "annualReturnRate",
            "monthly_contribution": "monthlyContribution",
            "quantity": "quantity",
            "as_of_date": "asOfDate",
        }
    for col, field in optional_map.items():
        val = row.get(col)
        if val is not None:
            # Money columns stay Decimal (Numeric); refs stay int. No float
            # coercion — see calculations._money. Date columns are exposed as
            # ISO strings in the dict API.
            entry[field] = val.isoformat() if col in _DATE_COLS else val
    # Store DB id for index operations (not exposed in TypedDict)
    entry["_db_id"] = row["id"]
    return entry


def get_asset_entries(conn: Connection, snapshot_id: int) -> list[AssetEntry]:
    rows = load_holdings(conn, snapshot_id, _ASSET_GROUPS)
    return [_row_to_asset_entry(r) for r in rows]


def _split_entry(entry: dict[str, Any], group: str) -> tuple[dict, dict]:
    """(spine, detail) column dicts for an insert, from a dict-API entry."""
    spine: dict[str, Any] = {"name": entry.get("name", "")}
    if group == "loan":
        spine["type"] = "loan"
    elif entry.get("type") is not None:
        if entry["type"] not in ASSET_GROUP_TYPES:
            raise ValueError(f"Invalid asset type: {entry['type']}")
        spine["type"] = entry["type"]
    for field in ("institution", "asOfDate"):
        val = entry.get(field)
        if val is not None:
            spine[_SPINE_FIELD_TO_COL[field]] = (
                to_date(val) if field == "asOfDate" else val
            )
    detail: dict[str, Any] = {}
    for field, col in _DETAIL_FIELD_TO_COL[group].items():
        val = entry.get(field)
        if val is not None:
            detail[col] = to_date(val) if col in _DATE_COLS else val
    if group == "loan" and detail.get("balance") is not None:
        # Fold a legacy quantity into the total owed, then store signed.
        qty = entry.get("quantity")
        if qty is not None:
            detail["balance"] = detail["balance"] * qty
        detail["balance"] = -detail["balance"]
    return spine, detail


def add_asset_entry(
    conn: Connection, snapshot_id: int, entry: dict[str, Any]
) -> int | None:
    group = "loan" if entry.get("kind") == "debt" else "asset"
    spine, detail = _split_entry(entry, group)
    # IntegrityError propagates, matching the pre-split dict API.
    holding_id = insert_holding(conn, snapshot_id, group, spine, detail)
    conn.commit()
    if entry.get("kind") == "asset":
        return holding_id
    return None


def update_asset_entry(
    conn: Connection,
    snapshot_id: int,
    index: int,
    updates: dict[str, Any],
    delete_keys: list[str] | None = None,
) -> None:
    db_id = _index_to_db_id(conn, snapshot_id, index)
    row = get_holding(conn, snapshot_id, db_id)
    if row is None:
        raise ValueError(f"Assets index {index} out of range")
    group = row["group_key"]
    if "kind" in updates:
        raise ValueError("An entry's kind cannot be changed")
    if group == "loan" and updates.get("type") not in (None, "loan"):
        raise ValueError("A loan's type is fixed")
    dmap = _DETAIL_FIELD_TO_COL[group]

    merged = dict(row)
    spine_updates: dict[str, Any] = {}

    def _apply(field: str, val: Any) -> None:
        if field in _SPINE_FIELD_TO_COL:
            col = _SPINE_FIELD_TO_COL[field]
            spine_updates[col] = to_date(val) if col == "as_of_date" else val
            merged[col] = spine_updates[col]
        elif field in dmap:
            col = dmap[field]
            if col in _DATE_COLS:
                val = to_date(val)
            if group == "loan" and col == "balance":
                val = _negate(val)
            merged[col] = val

    for field, val in updates.items():
        _apply(field, val)
    for field in delete_keys or []:
        _apply(field, None)

    detail_values = {col: merged.get(col) for col in dmap.values()}
    # IntegrityError propagates, matching the pre-split dict API.
    update_holding(conn, snapshot_id, db_id, group, group, spine_updates, detail_values)
    conn.commit()


def delete_asset_entry(conn: Connection, snapshot_id: int, index: int) -> None:
    db_id = _index_to_db_id(conn, snapshot_id, index)
    try:
        delete_holding(conn, snapshot_id, db_id, _ASSET_GROUPS)
    except IntegrityError as e:
        # NO ACTION foreign key: this asset is still referenced by a debt
        # (assetRef) or the holding by a budget entry.
        conn.rollback()
        raise ValueError(
            f"Asset id {db_id} is referenced by a debt; remove or change assetRef first"
        ) from e
    conn.commit()


def move_asset_entry(
    conn: Connection, snapshot_id: int, index: int, direction: str
) -> None:
    db_id = _index_to_db_id(conn, snapshot_id, index)
    swap_adjacent(conn, snapshot_id, _ASSET_GROUPS, db_id, direction)
    conn.commit()


def reorder_asset_entries(
    conn: Connection, snapshot_id: int, new_order: list[int]
) -> None:
    """Persist a drag-reordered entry order (permutation of the merged
    loan + asset list, group-locally decomposed)."""
    reorder_merged(conn, snapshot_id, _ASSET_GROUPS, new_order)
    conn.commit()


def _index_to_db_id(conn: Connection, snapshot_id: int, index: int) -> int:
    rows = load_holdings(conn, snapshot_id, _ASSET_GROUPS)
    if index < 0 or index >= len(rows):
        raise ValueError(f"Assets index {index} out of range (0..{len(rows) - 1})")
    return rows[index]["id"]
