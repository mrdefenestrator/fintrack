"""Shared utilities for web routes."""

import re
from datetime import date, datetime

from flask import abort, current_app, g

from fintrack.core import tables
from fintrack.core.loader import load_finances_from_db
from fintrack.networth import calculations
from fintrack.snapshots.repository import get_snapshot_id, list_snapshots


def snapshot_scoped(bp):
    """Scope a blueprint mounted at /s/<filename> to the active snapshot.

    Pops the filename path segment into g (validated to g.snapshot_id) so view
    functions keep their original signatures, and re-injects it into url_for.
    Used by the ledger blueprints; the finances-style blueprints take filename
    as an explicit view argument instead.
    """

    @bp.url_value_preprocessor
    def _pull_filename(endpoint, values):
        filename = values.pop("filename")
        g.filename = filename
        g.snapshot_id = validate_snapshot(filename)

    @bp.url_defaults
    def _push_filename(endpoint, values):
        values.setdefault("filename", getattr(g, "filename", None))

    return bp


def validate_snapshot(filename: str) -> int:
    """Validate a URL filename component and return the snapshot id.

    Aborts 400 if the name is syntactically invalid, 404 if no snapshot exists.
    Flask's <string:filename> converter already rejects forward slashes.
    """
    if "\\" in filename or ".." in filename:
        abort(400, description="Invalid snapshot name")
    if not re.match(r"^[a-zA-Z0-9 _\-.]+$", filename):
        abort(400, description="Invalid snapshot name")
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        snapshot_id = get_snapshot_id(conn, filename)
    if snapshot_id is None:
        abort(404, description=f"Snapshot not found: {filename}")
    return snapshot_id


def get_default_filename() -> str:
    """Return the name of the first available snapshot, or ''."""
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        names = list_snapshots(conn)
    return names[0] if names else ""


def quick_totals(data: dict, today: date | None = None) -> dict:
    """The header quick totals shown on the Accounts/Budget/Assets nav tabs."""
    today = today or datetime.now().astimezone().date()
    return {
        "n2": calculations.liquid_minus_cc(data.get("accounts") or []),
        "n3": calculations.projected_change_to_eom(
            data.get("budget") or [], today.year, today.month, today.day
        ),
        "n6": calculations.net_nonliquid_total(
            data.get("assets") or [], rates=data.get("rates")
        ),
    }


def get_common_context(snapshot_id: int, filename: str, edit_mode: bool):
    """Data and computed values shared by all views."""
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        data = load_finances_from_db(conn, snapshot_id)
        available_files = list_snapshots(conn)

    accs = data.get("accounts") or []
    budget = data.get("budget") or []
    assets = data.get("assets") or []
    rates = data.get("rates") or {}
    rate_meta = data.get("rate_meta") or {}

    today = datetime.now().astimezone().date()
    year, month, day = today.year, today.month, today.day

    totals = quick_totals(data, today)
    n2, n3, n6 = totals["n2"], totals["n3"], totals["n6"]
    account_display = tables._account_display_by_id(accs)

    return {
        "snapshot_id": snapshot_id,
        "filename": filename,
        "active_file": filename,
        "available_files": available_files,
        "as_of_date": today.isoformat(),
        "budget_label": f"budget to end of {today.strftime('%b %Y')}",
        "accounts": accs,
        "budget": budget,
        "assets": assets,
        "rates": rates,
        "rate_meta": rate_meta,
        "year": year,
        "month": month,
        "day": day,
        "n2": n2,
        "n3": n3,
        "n6": n6,
        "account_display_by_id": account_display,
        "edit_mode": edit_mode,
    }


def drop_separator_rows(rows):
    """Remove separator rows (all cells are dashes) from table rows."""
    return [
        row
        for row in rows
        if not all(isinstance(c, str) and set(c.strip()) <= {"-"} for c in row)
    ]


# =============================================================================
# Accounts table configuration
# =============================================================================

# Column mapping for accounts table (index -> field name)
ACCOUNTS_FIELD_TO_COL = [
    "name",
    "institution",
    "type",
    "balance",
    "limit",
    "available",
    "rewards_balance",
    "statement_balance",
    "statement_due_day_of_month",
    "paymentAccountRef",
    "minimum_balance",
]

# Right-aligned column indices for accounts
ACCOUNTS_RIGHT_ALIGN_COLS = (3, 4, 5, 6, 7, 8, 10)

# Fields that are right-aligned in accounts
ACCOUNTS_RIGHT_ALIGN_FIELDS = {
    "balance",
    "limit",
    "available",
    "rewards_balance",
    "statement_balance",
    "statement_due_day_of_month",
    "minimum_balance",
}

# Account types for which the Reserve column (accounts.minimum_balance) is
# meaningful and therefore editable: cash-like accounts a household might
# want to keep a floor balance in. Credit cards, loans, gift cards, and
# "other" have no such reserve concept.
RESERVE_EDITABLE_TYPES = {"checking", "savings", "wallet", "digital_wallet"}


def account_field_editable(acc, field: str) -> bool:
    """Credit card rows: balance not editable (calculated); only CC can edit
    limit, available, rewards, statement, due. Non-CC: balance editable;
    limit/available/rewards/statement/due not. Reserve (minimum_balance) is
    editable only for account types where a reserve is meaningful."""
    is_cc = acc.get("type") == "credit_card"
    if field == "minimum_balance":
        return acc.get("type") in RESERVE_EDITABLE_TYPES
    if is_cc and field == "balance":
        return False
    return is_cc or field not in (
        "limit",
        "available",
        "rewards_balance",
        "statement_balance",
        "statement_due_day_of_month",
        "paymentAccountRef",
    )


def account_field_right_align(field: str) -> bool:
    """Return True if field should be right-aligned."""
    return field in ACCOUNTS_RIGHT_ALIGN_FIELDS


# =============================================================================
# Budget table configuration (income/expense)
# =============================================================================

# Column mapping for budget table (index -> field name, None = computed/non-editable)
PROJECTION_FIELD_TO_COL = [
    None,  # Kind (income/expense label)
    "description",
    "type",
    "amount",
    None,  # Subtotal (computed)
    "recurrence",
    "dayOfMonth",
    "autoAccountRef",
]

# Right-aligned column indices for budget
BUDGET_RIGHT_ALIGN_COLS = (3, 4, 6)

# Fields that are right-aligned in budget
BUDGET_RIGHT_ALIGN_FIELDS = {"amount", "dayOfMonth", "month", "dayOfYear"}


def budget_field_editable(kind: str, field: str) -> bool:
    """Return True if the field is editable for this budget entry kind.
    Kind column (0) and Subtotal column (4) are never editable."""
    # These fields are always editable for both income and expense
    editable_fields = {
        "description",
        "type",
        "amount",
        "recurrence",
        "dayOfMonth",
        "month",
        "dayOfYear",
        "autoAccountRef",
        "date",
    }
    return field in editable_fields


def budget_field_right_align(field: str) -> bool:
    """Return True if field should be right-aligned."""
    return field in BUDGET_RIGHT_ALIGN_FIELDS


# =============================================================================
# Assets table configuration (assets/debts)
# =============================================================================

# Column mapping for assets table - different for asset vs debt
# (index -> field name, None = non-editable for that kind)
ASSETS_FIELD_TO_COL_ASSET = [
    None,  # Kind label
    "institution",
    "name",
    "value",
    "quantity",
    None,  # Subtotal (computed)
    "source",
    None,  # Interest (N/A for assets)
]

ASSETS_FIELD_TO_COL_DEBT = [
    None,  # Kind label
    "institution",
    "name",
    "balance",  # per-unit balance
    "quantity",  # quantity
    None,  # Subtotal — computed
    "assetRef",
    "interestRate",
]

# Right-aligned column indices for assets
ASSETS_RIGHT_ALIGN_COLS = (3, 4, 5, 7)

# Fields that are right-aligned in assets
ASSETS_RIGHT_ALIGN_FIELDS = {
    "value",
    "quantity",
    "balance",
    "interestRate",
    "annualReturnRate",
    "monthlyContribution",
}


def assets_field_editable(kind: str, field: str) -> bool:
    """Return True if the field is editable for this asset/debt kind."""
    if kind == "asset":
        editable_fields = {
            "institution",
            "name",
            "value",
            "quantity",
            "source",
            "annualReturnRate",
            "monthlyContribution",
        }
    else:  # debt
        editable_fields = {
            "institution",
            "name",
            "balance",
            "quantity",
            "assetRef",
            "interestRate",
        }
    return field in editable_fields


def assets_field_right_align(field: str) -> bool:
    """Return True if field should be right-aligned."""
    return field in ASSETS_RIGHT_ALIGN_FIELDS


def assets_get_field_for_col(kind: str, col_index: int) -> str | None:
    """Get the field name for a column index, or None if not editable."""
    if kind == "asset":
        if 0 <= col_index < len(ASSETS_FIELD_TO_COL_ASSET):
            return ASSETS_FIELD_TO_COL_ASSET[col_index]
    else:  # debt
        if 0 <= col_index < len(ASSETS_FIELD_TO_COL_DEBT):
            return ASSETS_FIELD_TO_COL_DEBT[col_index]
    return None
