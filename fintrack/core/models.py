"""SQLAlchemy Core table definitions for the unified fintrack schema.

Merges the spending ledger tables (imports/transactions/merchant_cache/
transaction_corrections/categories) with the finances net-worth tables
(snapshots/accounts/budget_entries/asset_entries) and adds balance_history.

Conventions:
- Every household-owned row is scoped to a snapshot, directly (accounts,
  budget_entries, asset_entries) or via its account (imports, transactions,
  balance_history). merchant_cache, transaction_corrections, and categories
  are deliberately global: classification knowledge is shared across
  snapshots.
- accounts.balance is the canonical signed balance for every account type
  (negative = amount owed on credit cards). available/credit_limit are
  editable credit-card metadata from which balance is derived when saved.
- snapshot_id foreign keys cascade; ownership references between rows
  (payment_account_ref, auto_account_ref, asset_ref) use NO ACTION so a
  direct delete of a referenced row is blocked while a snapshot-level
  cascade still cleans up.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData()


def _utcnow():
    return datetime.now(timezone.utc)


snapshots = Table(
    "snapshots",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, unique=True, nullable=False),
    Column("created_at", DateTime, default=_utcnow),
)

accounts = Table(
    "accounts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "snapshot_id",
        Integer,
        ForeignKey("snapshots.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("name", String, nullable=False),
    Column("institution", String, nullable=True),
    Column("account_type", String, nullable=False),
    Column("balance", Numeric(12, 2), nullable=True),
    Column("credit_limit", Numeric(12, 2), nullable=True),
    Column("available", Numeric(12, 2), nullable=True),
    Column("rewards_balance", Numeric(12, 2), nullable=True),
    Column("statement_balance", Numeric(12, 2), nullable=True),
    Column("statement_due_day_of_month", Integer, nullable=True),
    Column("payment_account_ref", Integer, ForeignKey("accounts.id"), nullable=True),
    Column("as_of_date", Date, nullable=True),
    Column("minimum_balance", Numeric(12, 2), nullable=True),
    Column("sort_order", Integer, nullable=False, default=0),
    Column("created_at", DateTime, default=_utcnow),
    UniqueConstraint(
        "snapshot_id",
        "institution",
        "name",
        name="uq_accounts_snapshot_institution_name",
    ),
)

imports = Table(
    "imports",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "account_id",
        Integer,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("filename", String, nullable=False),
    Column("file_hash", String, nullable=False),
    Column("imported_at", DateTime, default=_utcnow),
    Column("status", String, nullable=False, default="staging"),
    Column("ledger_balance", Numeric(12, 2), nullable=True),
    Column("ledger_balance_date", Date, nullable=True),
    Column("available_balance", Numeric(12, 2), nullable=True),
    Column("available_balance_date", Date, nullable=True),
    Column("beginning_balance", Numeric(12, 2), nullable=True),
)

transactions = Table(
    "transactions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "import_id",
        Integer,
        ForeignKey("imports.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "account_id",
        Integer,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("date", Date, nullable=False),
    Column("amount", Numeric(12, 2), nullable=False),
    Column("raw_description", String, nullable=False),
    Column("normalized_merchant", String, nullable=False),
    Column("fingerprint", String, nullable=False),
    Column("created_at", DateTime, default=_utcnow),
    Index("ix_transactions_fingerprint", "fingerprint"),
    Index("ix_transactions_account_date", "account_id", "date"),
)

merchant_cache = Table(
    "merchant_cache",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("merchant_name", String, unique=True, nullable=False),
    Column("category", String, nullable=False),
    Column("source", String, nullable=False),
    Column("created_at", DateTime, default=_utcnow),
    Column("updated_at", DateTime, default=_utcnow),
)

transaction_corrections = Table(
    "transaction_corrections",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "transaction_id",
        Integer,
        ForeignKey("transactions.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    ),
    Column("category", String, nullable=True),
    Column("merchant_name", String, nullable=True),
    Column("notes", String, nullable=True),
    Column("created_at", DateTime, default=_utcnow),
)

categories = Table(
    "categories",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, unique=True, nullable=False),
    Column("sort_order", Integer, nullable=False),
)

budget_entries = Table(
    "budget_entries",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "snapshot_id",
        Integer,
        ForeignKey("snapshots.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("kind", String, nullable=False),
    Column("description", String, nullable=False),
    Column("amount", Numeric(12, 2), nullable=False),
    Column("recurrence", String, nullable=False),
    Column("type", String, nullable=True),
    # Ledger category this scheduled entry covers; lets projections avoid
    # double-counting a budgeted expense against estimated category spend.
    Column("category", String, nullable=True),
    Column("date", Date, nullable=True),
    Column("day_of_month", Integer, nullable=True),
    Column("month", Integer, nullable=True),
    Column("day_of_year", Integer, nullable=True),
    Column("continuous", Boolean, nullable=True),
    Column("auto_account_ref", Integer, ForeignKey("accounts.id"), nullable=True),
    Column("sort_order", Integer, nullable=False, default=0),
)

asset_entries = Table(
    "asset_entries",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "snapshot_id",
        Integer,
        ForeignKey("snapshots.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("kind", String, nullable=False),
    Column("name", String, nullable=False),
    Column("institution", String, nullable=True),
    Column("value", Numeric(14, 2), nullable=True),
    Column("source", String, nullable=True),
    Column("quantity", Numeric(12, 4), nullable=True),
    Column("balance", Numeric(14, 2), nullable=True),
    Column("asset_ref", Integer, ForeignKey("asset_entries.id"), nullable=True),
    Column("interest_rate", Numeric(8, 6), nullable=True),
    Column("next_due_date", Date, nullable=True),
    Column("as_of_date", Date, nullable=True),
    Column("sort_order", Integer, nullable=False, default=0),
)

balance_history = Table(
    "balance_history",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "account_id",
        Integer,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("as_of", Date, nullable=False),
    Column("balance", Numeric(12, 2), nullable=False),
    Column("available", Numeric(12, 2), nullable=True),
    Column("source", String, nullable=False),  # statement | manual | migration
    Column("import_id", Integer, ForeignKey("imports.id"), nullable=True),
    Column("note", String, nullable=True),
    Column("created_at", DateTime, default=_utcnow),
    UniqueConstraint("account_id", "as_of", "source", name="uq_balance_history_point"),
)
