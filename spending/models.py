from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Date,
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


accounts = Table(
    "accounts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, unique=True, nullable=False),
    Column("institution", String, nullable=False),
    Column("account_type", String, nullable=False),
    Column("created_at", DateTime, default=_utcnow),
)

imports = Table(
    "imports",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
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
    Column("import_id", Integer, ForeignKey("imports.id"), nullable=False),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    Column("date", Date, nullable=False),
    Column("amount", Numeric(10, 2), nullable=False),
    Column("raw_description", String, nullable=False),
    Column("normalized_merchant", String, nullable=False),
    Column("fingerprint", String, nullable=False),
    Column("created_at", DateTime, default=_utcnow),
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
        ForeignKey("transactions.id"),
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
