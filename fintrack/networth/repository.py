"""Asset/debt repository: CRUD + move for asset and loan holdings.

Serves the unified loan group (which includes loans formerly tracked as
accounts) as `kind="debt"` dicts and the asset group as `kind="asset"`
dicts. The dict API keeps the historical positive amount-owed convention for
a debt's `balance`; the stored loan_details.balance is signed (negative =
owed) like every other balance, and the sign flips at this boundary.

Every value change is a dated point in balance_history (issue #107): a loan's
signed balance, or an asset's value (quantity x cached price for a non-USD
unit, else its manual value). Adds and edits record a 'manual' point; a price
refresh records a 'price' point for each asset held in that symbol
(`record_price_points`).

Debts no longer carry unit/quantity (docs/notes-schema-split.md D2): an add
with a quantity folds it into the balance; on existing rows the fields are
gone.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, insert, select
from sqlalchemy.exc import IntegrityError

from fintrack.accounts.balance_history import latest_point, record_balance
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
from fintrack.core.models import asset_details, balance_history, holdings
from fintrack.core.types import ASSET_GROUP_TYPES, AssetEntry
from fintrack.networth.calculations import _entry_subtotal

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
        # The holding id, on every entry: an asset's is the target a debt's
        # assetRef points at; both are the handle the repository mutates by.
        "id": row["id"],
    }
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
    return entry


def get_asset_entries(conn: Connection, snapshot_id: int) -> list[AssetEntry]:
    rows = load_holdings(conn, snapshot_id, _ASSET_GROUPS)
    return [_row_to_asset_entry(r) for r in rows]


def _holding_value(conn: Connection, row: dict[str, Any]) -> Decimal | None:
    """The value a holding's history records: a loan's signed balance (None
    when unset), or an asset's value at the cached price (never fetches)."""
    if row["group_key"] == "loan":
        return row.get("balance")
    from fintrack.networth.prices import cached_rates

    unit = row.get("unit") or "USD"
    rates = cached_rates(conn, {unit}) if unit != "USD" else {}
    value = _entry_subtotal(_row_to_asset_entry(row), rates)
    return value.quantize(Decimal("0.01"))


def _record_value(
    conn: Connection,
    snapshot_id: int,
    holding_id: int,
    *,
    as_of: date | None,
    previous: Decimal | None = None,
) -> None:
    """Record the holding's current value as a manual point, unless it is
    unset or unchanged from `previous`."""
    row = get_holding(conn, snapshot_id, holding_id)
    value = _holding_value(conn, row) if row is not None else None
    if value is None or value == previous:
        return
    record_balance(
        conn, account_id=holding_id, balance=value, as_of=as_of, source="manual"
    )


def record_price_points(
    conn: Connection, prices: dict[str, Decimal], *, today: date | None = None
) -> None:
    """Re-value every asset held in a refreshed symbol, in every snapshot (the
    price cache is global): one 'price' point per holding per day, skipped
    when the value didn't change."""
    if not prices:
        return
    rows = conn.execute(
        select(
            asset_details.c.holding_id,
            asset_details.c.unit,
            asset_details.c.quantity,
        ).where(asset_details.c.unit.in_(list(prices)))
    ).all()
    for holding_id, unit, quantity in rows:
        qty = Decimal(str(quantity)) if quantity is not None else Decimal(1)
        value = (qty * prices[unit]).quantize(Decimal("0.01"))
        latest = latest_point(conn, holding_id)
        if latest is not None and Decimal(str(latest["balance"])) == value:
            continue
        record_balance(
            conn, account_id=holding_id, balance=value, as_of=today, source="price"
        )


def record_baselines(conn: Connection) -> list[int]:
    """Give every loan and asset with no history a 'baseline' point at its
    current value, dated its As Of (or today) — the starting point migration
    e7f8a9b0c1d2 seeds, for holdings that bypass the repository (the legacy
    and YAML imports). Doesn't commit or re-sync the holding rows: the caller
    does both (the legacy import runs in one transaction so --dry-run can roll
    back). Returns the holdings that got a point."""
    has_history = select(balance_history.c.account_id).distinct()
    rows = conn.execute(
        select(holdings.c.id, holdings.c.snapshot_id).where(
            holdings.c.group_key.in_(_ASSET_GROUPS),
            holdings.c.id.not_in(has_history),
        )
    ).all()
    today = datetime.now().astimezone().date()
    recorded: list[int] = []
    for holding_id, snapshot_id in rows:
        row = get_holding(conn, snapshot_id, holding_id)
        value = _holding_value(conn, row)
        if value is None:
            continue
        conn.execute(
            insert(balance_history).values(
                account_id=holding_id,
                as_of=row.get("as_of_date") or today,
                balance=value,
                source="baseline",
            )
        )
        recorded.append(holding_id)
    return recorded


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
    conn: Connection,
    snapshot_id: int,
    entry: dict[str, Any],
    *,
    today: date | None = None,
) -> int | None:
    """Add an asset or debt and record its starting value, dated its As Of
    or else `today` (the caller's local date; the server's when omitted)."""
    group = "loan" if entry.get("kind") == "debt" else "asset"
    spine, detail = _split_entry(entry, group)
    # IntegrityError propagates, matching the pre-split dict API.
    holding_id = insert_holding(conn, snapshot_id, group, spine, detail)
    conn.commit()
    _record_value(conn, snapshot_id, holding_id, as_of=spine.get("as_of_date") or today)
    if entry.get("kind") == "asset":
        return holding_id
    return None


def update_asset_entry(
    conn: Connection,
    snapshot_id: int,
    holding_id: int,
    updates: dict[str, Any],
    delete_keys: list[str] | None = None,
    *,
    today: date | None = None,
) -> None:
    """Update an asset or debt. A change to its value (a loan's balance; an
    asset's value, quantity or unit) records a manual point dated the edit's
    As Of, or else `today` (the caller's local date; the server's when
    omitted)."""
    row = get_holding(conn, snapshot_id, holding_id)
    if row is None or row["group_key"] not in _ASSET_GROUPS:
        raise ValueError(f"Asset/debt id {holding_id} not found")
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
    update_holding(
        conn, snapshot_id, holding_id, group, group, spine_updates, detail_values
    )
    conn.commit()
    _record_value(
        conn,
        snapshot_id,
        holding_id,
        as_of=spine_updates.get("as_of_date") or today,
        previous=_holding_value(conn, row),
    )


def delete_asset_entry(conn: Connection, snapshot_id: int, holding_id: int) -> None:
    try:
        deleted = delete_holding(conn, snapshot_id, holding_id, _ASSET_GROUPS)
    except IntegrityError as e:
        # NO ACTION foreign key: this asset is still referenced by a debt
        # (assetRef) or the holding by a budget entry.
        conn.rollback()
        raise ValueError(
            f"Asset id {holding_id} is referenced by a debt; "
            "remove or change assetRef first"
        ) from e
    if deleted == 0:
        raise ValueError(f"Asset/debt id {holding_id} not found")
    conn.commit()


def move_asset_entry(
    conn: Connection, snapshot_id: int, holding_id: int, direction: str
) -> None:
    swap_adjacent(conn, snapshot_id, _ASSET_GROUPS, holding_id, direction)
    conn.commit()


def reorder_asset_entries(
    conn: Connection, snapshot_id: int, new_order: list[int]
) -> None:
    """Persist a drag-reordered entry order (permutation of the merged
    loan + asset list, group-locally decomposed)."""
    reorder_merged(conn, snapshot_id, _ASSET_GROUPS, new_order)
    conn.commit()
