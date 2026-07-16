"""SQLAlchemy Core table definitions for finances storage."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
)

metadata = MetaData()


def _utcnow():
    return datetime.now(timezone.utc)


snapshots = Table(
    "fin_snapshots",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, unique=True, nullable=False),
    Column("created_at", DateTime, default=_utcnow),
)

accounts = Table(
    "fin_accounts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "snapshot_id",
        Integer,
        ForeignKey("fin_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("name", String, nullable=False),
    Column("type", String, nullable=False),
    Column("balance", Numeric(12, 2), nullable=True),
    Column("limit", Numeric(12, 2), nullable=True),
    Column("available", Numeric(12, 2), nullable=True),
    Column("rewards_balance", Numeric(12, 2), nullable=True),
    Column("statement_balance", Numeric(12, 2), nullable=True),
    Column("statement_due_day_of_month", Integer, nullable=True),
    # account.id of the payment source (same snapshot). NO ACTION: blocks
    # deleting a referenced account, but lets a snapshot cascade delete both.
    Column(
        "payment_account_ref", Integer, ForeignKey("fin_accounts.id"), nullable=True
    ),
    Column("as_of_date", String, nullable=True),
    Column("minimum_balance", Numeric(12, 2), nullable=True),
    Column("institution", String, nullable=True),
    Column("partial_account_number", String, nullable=True),
    Column("sort_order", Integer, nullable=False, default=0),
)

budget_entries = Table(
    "fin_budget_entries",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "snapshot_id",
        Integer,
        ForeignKey("fin_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("kind", String, nullable=False),
    Column("description", String, nullable=False),
    Column("amount", Numeric(12, 2), nullable=False),
    Column("recurrence", String, nullable=False),
    Column("type", String, nullable=True),
    Column("date", String, nullable=True),
    Column("day_of_month", Integer, nullable=True),
    Column("month", Integer, nullable=True),
    Column("day_of_year", Integer, nullable=True),
    Column("continuous", Boolean, nullable=True),
    # accounts.id of the deposit/payment account (same snapshot); see note above
    Column("auto_account_ref", Integer, ForeignKey("fin_accounts.id"), nullable=True),
    Column("sort_order", Integer, nullable=False, default=0),
)

asset_entries = Table(
    "fin_asset_entries",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "snapshot_id",
        Integer,
        ForeignKey("fin_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("kind", String, nullable=False),
    Column("name", String, nullable=False),
    Column("institution", String, nullable=True),
    Column("value", Numeric(14, 2), nullable=True),
    Column("source", String, nullable=True),
    Column("quantity", Numeric(12, 4), nullable=True),
    Column("balance", Numeric(14, 2), nullable=True),
    # asset_entries.id of the paired asset (for kind='debt', same snapshot); see note above
    Column("asset_ref", Integer, ForeignKey("fin_asset_entries.id"), nullable=True),
    Column("interest_rate", Numeric(8, 6), nullable=True),
    Column("next_due_date", String, nullable=True),
    Column("as_of_date", String, nullable=True),
    Column("sort_order", Integer, nullable=False, default=0),
)
