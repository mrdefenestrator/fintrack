"""SQLAlchemy Core table definitions for the unified fintrack schema.

Holdings are stored as a supertype/subtype split (docs/notes-schema-split.md):
a slim `holdings` table carries the spine every holding shares (snapshot,
group, type, institution, name, as_of_date, sort order) and four detail
tables — cash_details, credit_card_details, loan_details, asset_details —
carry each group's own columns. The ledger tables (imports, transactions,
merchant_cache, transaction_corrections, categories) and the budget/history
tables reference the supertype, so every foreign key has a single real
target.

Conventions:
- Every household-owned row is scoped to a snapshot, directly (holdings,
  budget_entries) or via its holding (imports, transactions,
  balance_history). merchant_cache, transaction_corrections, and categories
  are deliberately global: classification knowledge is shared across
  snapshots.
- Detail tables carry denormalized group_key and snapshot_id copies purely
  as composite-FK material: (holding_id, group_key) proves the parent is in
  the right group, and (holding_id, snapshot_id) both scopes cleanup (its
  CASCADE) and lets references INTO a detail table prove they stay inside
  one snapshot. ON UPDATE CASCADE on the group FK means a group change on
  the parent is rejected (the cascaded group_key would violate the detail
  table's CHECK) unless the detail row is moved first — the transition
  discipline the repositories implement (delete old detail -> update parent
  -> insert new detail).
- Balances are signed for every group that has one (negative = amount owed
  on credit cards and loans); each is a denormalized cache of the latest
  balance_history point — always write through record_balance(), never
  update the column directly. Credit-card `available` is not stored: it is
  computed as credit_limit + balance.
- snapshot_id foreign keys cascade; ownership references between rows
  (payment_account_ref, secured_asset_ref, auto_account_ref) use NO ACTION
  so a direct delete of a referenced row is blocked while a snapshot-level
  cascade still cleans up.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
)

from fintrack.core.types import (
    ASSET_GROUP_TYPES,
    CASH_TYPES,
    GROUP_KEYS,
    IMPORTABLE_GROUPS,
)

metadata = MetaData()


def _utcnow():
    return datetime.now(UTC)


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


snapshots = Table(
    "snapshots",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, unique=True, nullable=False),
    Column("created_at", DateTime, default=_utcnow),
)

holdings = Table(
    "holdings",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "snapshot_id",
        Integer,
        ForeignKey("snapshots.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("group_key", String, nullable=False),
    # Holding type (fintrack.core.types.HOLDING_TYPE_TIER). Pinned to the
    # group for credit cards and loans; only the asset group may leave it
    # NULL (unclassified -> DEFAULT_TIER).
    Column("type", String, nullable=True),
    Column("name", String, nullable=False),
    Column("institution", String, nullable=True),
    Column("as_of_date", Date, nullable=True),
    # Scoped per (snapshot_id, group_key): each Holdings band orders its own
    # rows independently.
    Column("sort_order", Integer, nullable=False, default=0),
    Column("created_at", DateTime, default=_utcnow),
    CheckConstraint(
        f"group_key IN ({_sql_list(GROUP_KEYS)})",
        name="ck_holdings_group",
    ),
    CheckConstraint(
        "(group_key = 'credit_card' AND type = 'credit_card')"
        " OR (group_key = 'loan' AND type = 'loan')"
        f" OR (group_key = 'cash' AND type IN ({_sql_list(CASH_TYPES)}))"
        " OR (group_key = 'asset' AND (type IS NULL"
        f" OR type IN ({_sql_list(ASSET_GROUP_TYPES)})))",
        name="ck_holdings_type_matches_group",
    ),
    # Composite-FK targets: (id, group_key) lets children prove the parent's
    # group; (id, snapshot_id) lets references prove they stay in-snapshot.
    UniqueConstraint("id", "group_key", name="uq_holdings_id_group"),
    UniqueConstraint("id", "snapshot_id", name="uq_holdings_id_snapshot"),
    Index("ix_holdings_snapshot_group_sort", "snapshot_id", "group_key", "sort_order"),
)

# Statement-import identity: importable holdings stay uniquely nameable per
# (snapshot, institution, name); assets keep their old no-uniqueness behavior.
# Load-bearing in web/routes/imports.py:create_account (IntegrityError catch).
Index(
    "uq_holdings_importable_name",
    holdings.c.snapshot_id,
    holdings.c.institution,
    holdings.c.name,
    unique=True,
    sqlite_where=holdings.c.group_key != "asset",
)


def _subtype_columns(group: str) -> list:
    """The shared preamble of every detail table (see module docstring)."""
    return [
        Column("holding_id", Integer, primary_key=True),
        Column("group_key", String, nullable=False, server_default=group),
        Column("snapshot_id", Integer, nullable=False),
        CheckConstraint(f"group_key = '{group}'", name=f"ck_{group}_details_group"),
        ForeignKeyConstraint(
            ["holding_id", "group_key"],
            ["holdings.id", "holdings.group_key"],
            onupdate="CASCADE",
            name=f"fk_{group}_details_group",
        ),
        ForeignKeyConstraint(
            ["holding_id", "snapshot_id"],
            ["holdings.id", "holdings.snapshot_id"],
            ondelete="CASCADE",
            name=f"fk_{group}_details_snapshot",
        ),
        # Composite-FK target for typed, same-snapshot references INTO this
        # group (a card's payment account, a loan's secured asset).
        UniqueConstraint(
            "holding_id", "snapshot_id", name=f"uq_{group}_details_id_snapshot"
        ),
    ]


cash_details = Table(
    "cash_details",
    metadata,
    *_subtype_columns("cash"),
    Column("balance", Numeric(14, 2), nullable=True),
    Column("minimum_balance", Numeric(14, 2), nullable=True),  # reserve target
)

asset_details = Table(
    "asset_details",
    metadata,
    *_subtype_columns("asset"),
    # Denomination of the row's quantity: "USD" (the default — price is 1, so
    # amount == value) or a ticker/symbol (AAPL, BTC, …) whose per-unit price
    # comes from the price cache. amount = quantity * unit price.
    Column("unit", String, nullable=False, server_default="USD"),
    Column("quantity", Numeric(12, 4), nullable=True),  # default-1 semantics
    Column("value", Numeric(14, 2), nullable=True),  # per-unit value (manual)
    Column("source", String, nullable=True),  # valuation source
    Column("annual_return_rate", Numeric(8, 6), nullable=True),
    Column("monthly_contribution", Numeric(14, 2), nullable=True),
)

credit_card_details = Table(
    "credit_card_details",
    metadata,
    *_subtype_columns("credit_card"),
    Column("balance", Numeric(14, 2), nullable=True),  # signed; negative = owed
    Column("credit_limit", Numeric(14, 2), nullable=True),
    # No `available` column: available = credit_limit + balance, computed.
    Column("rewards_balance", Numeric(14, 2), nullable=True),
    Column("statement_balance", Numeric(14, 2), nullable=True),
    Column("statement_due_day_of_month", Integer, nullable=True),
    Column("payment_account_ref", Integer, nullable=True),
    CheckConstraint(
        "statement_due_day_of_month IS NULL"
        " OR statement_due_day_of_month BETWEEN 1 AND 31",
        name="ck_credit_card_due_day",
    ),
    # A card's payment account must be a cash holding in the same snapshot.
    ForeignKeyConstraint(
        ["payment_account_ref", "snapshot_id"],
        ["cash_details.holding_id", "cash_details.snapshot_id"],
        name="fk_credit_card_payment_account",
    ),
)

loan_details = Table(
    "loan_details",
    metadata,
    *_subtype_columns("loan"),
    Column("balance", Numeric(14, 2), nullable=True),  # signed; negative = owed
    Column("interest_rate", Numeric(8, 6), nullable=True),
    Column("original_principal", Numeric(14, 2), nullable=True),
    Column("term_months", Integer, nullable=True),
    Column("origination_date", Date, nullable=True),
    Column("statement_due_day_of_month", Integer, nullable=True),
    # A loan may carry either or both linked holdings: the cash account it is
    # paid from, and the asset it is secured by.
    Column("payment_account_ref", Integer, nullable=True),
    Column("secured_asset_ref", Integer, nullable=True),
    CheckConstraint(
        "statement_due_day_of_month IS NULL"
        " OR statement_due_day_of_month BETWEEN 1 AND 31",
        name="ck_loan_due_day",
    ),
    CheckConstraint(
        "original_principal IS NULL OR original_principal > 0",
        name="ck_loan_original_principal",
    ),
    CheckConstraint(
        "term_months IS NULL OR term_months > 0",
        name="ck_loan_term_months",
    ),
    ForeignKeyConstraint(
        ["payment_account_ref", "snapshot_id"],
        ["cash_details.holding_id", "cash_details.snapshot_id"],
        name="fk_loan_payment_account",
    ),
    ForeignKeyConstraint(
        ["secured_asset_ref", "snapshot_id"],
        ["asset_details.holding_id", "asset_details.snapshot_id"],
        name="fk_loan_secured_asset",
    ),
)

imports = Table(
    "imports",
    metadata,
    Column("id", Integer, primary_key=True),
    # The importable holding this statement belongs to. The column keeps its
    # historical name; the denormalized holding_group copy (kept in sync by
    # ON UPDATE CASCADE) is what enforces importability at the DB: a holding
    # with import history can never be retyped into the asset group.
    Column("account_id", Integer, nullable=False),
    Column("holding_group", String, nullable=False),
    Column("filename", String, nullable=False),
    Column("file_hash", String, nullable=False),
    Column("imported_at", DateTime, default=_utcnow),
    Column("status", String, nullable=False, default="staging"),
    Column("ledger_balance", Numeric(14, 2), nullable=True),
    Column("ledger_balance_date", Date, nullable=True),
    Column("available_balance", Numeric(14, 2), nullable=True),
    Column("available_balance_date", Date, nullable=True),
    Column("beginning_balance", Numeric(14, 2), nullable=True),
    CheckConstraint(
        f"holding_group IN ({_sql_list(IMPORTABLE_GROUPS)})",
        name="ck_imports_importable_group",
    ),
    CheckConstraint(
        "status IN ('staging', 'confirmed', 'rejected')",
        name="ck_imports_status",
    ),
    ForeignKeyConstraint(
        ["account_id", "holding_group"],
        ["holdings.id", "holdings.group_key"],
        onupdate="CASCADE",
        ondelete="CASCADE",
        name="fk_imports_holding",
    ),
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
        ForeignKey("holdings.id", ondelete="CASCADE"),
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
    Column("category", String, nullable=True),
    Column("date", Date, nullable=True),
    Column("day_of_month", Integer, nullable=True),
    Column("month", Integer, nullable=True),
    Column("day_of_year", Integer, nullable=True),
    Column("continuous", Boolean, nullable=True),
    Column("auto_account_ref", Integer, nullable=True),
    Column("sort_order", Integer, nullable=False, default=0),
    CheckConstraint("kind IN ('income', 'expense')", name="ck_budget_kind"),
    CheckConstraint(
        "recurrence IN ('one_time', 'monthly', 'biweekly', 'quarterly',"
        " 'semiannual', 'annual')",
        name="ck_budget_recurrence",
    ),
    # The deposit/payment holding must live in the same snapshot (untyped on
    # purpose: an income deposits to cash, a CC-paid expense may reference
    # the card).
    ForeignKeyConstraint(
        ["auto_account_ref", "snapshot_id"],
        ["holdings.id", "holdings.snapshot_id"],
        name="fk_budget_auto_account",
    ),
)

price_cache = Table(
    "price_cache",
    metadata,
    Column("unit", String, primary_key=True),  # e.g. "BTC", "AAPL"
    Column("price_usd", Numeric(14, 6), nullable=False),
    Column("fetched_at", DateTime, nullable=False),
)

balance_history = Table(
    "balance_history",
    metadata,
    Column("id", Integer, primary_key=True),
    # Deliberately references any holding (not just importable groups) so
    # asset value history can land here later.
    Column(
        "account_id",
        Integer,
        ForeignKey("holdings.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("as_of", Date, nullable=False),
    Column("balance", Numeric(14, 2), nullable=False),
    Column("available", Numeric(14, 2), nullable=True),
    Column("source", String, nullable=False),  # statement | manual | migration
    Column("import_id", Integer, ForeignKey("imports.id"), nullable=True),
    Column("note", String, nullable=True),
    Column("created_at", DateTime, default=_utcnow),
    CheckConstraint(
        "source IN ('statement', 'manual', 'migration')",
        name="ck_balance_history_source",
    ),
    UniqueConstraint("account_id", "as_of", "source", name="uq_balance_history_point"),
)
