"""Fixtures for budget reconcile tests: in-memory DB with one snapshot, plus
helpers to seed accounts, budget entries, and confirmed transactions whose ids
the tests need (unlike the projections seeder, which is fire-and-forget)."""

import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, insert, select

from fintrack.accounts.repository import add_account
from fintrack.budget.repository import add_budget_entry, get_budget_entries
from fintrack.core.models import (
    holdings,
    imports,
    merchant_cache,
    metadata,
    transactions,
)
from fintrack.snapshots.repository import create_snapshot


@pytest.fixture
def engine():
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    # FK pragma isn't attached to this bare test engine; ON DELETE SET NULL is
    # exercised in the migration/route tests, not here.
    yield engine
    engine.dispose()


@pytest.fixture
def conn(engine):
    with engine.connect() as connection:
        yield connection


@pytest.fixture
def snapshot_id(conn):
    return create_snapshot(conn, "test")


@pytest.fixture
def account(conn, snapshot_id):
    return add_account(
        conn, snapshot_id, {"name": "Checking", "type": "checking", "balance": 0}
    )


def make_entry(conn, snapshot_id, **fields) -> int:
    """Add a budget entry and return its db id (_db_id)."""
    entry = {"kind": "expense", "description": "e", "recurrence": "monthly", **fields}
    add_budget_entry(conn, snapshot_id, entry)
    entries = get_budget_entries(conn, snapshot_id)
    return entries[-1]["_db_id"]


class TxnSeeder:
    """Insert confirmed transactions into one import, returning each row id."""

    def __init__(self, conn, account_id):
        self.conn = conn
        holding_group = conn.execute(
            select(holdings.c.group_key).where(holdings.c.id == account_id)
        ).scalar()
        self.account_id = account_id
        self.import_id = conn.execute(
            insert(imports).values(
                account_id=account_id,
                holding_group=holding_group,
                filename="seed.ofx",
                file_hash=uuid.uuid4().hex,
                status="confirmed",
            )
        ).inserted_primary_key[0]
        self._seen: set[str] = set()

    def add(
        self,
        txn_date: date,
        amount: str,
        merchant: str = "Merchant",
        category: str | None = None,
    ) -> int:
        txn_id = self.conn.execute(
            insert(transactions).values(
                import_id=self.import_id,
                account_id=self.account_id,
                date=txn_date,
                amount=amount,
                raw_description=merchant,
                normalized_merchant=merchant,
                fingerprint=uuid.uuid4().hex,
            )
        ).inserted_primary_key[0]
        if category and merchant not in self._seen:
            self._seen.add(merchant)
            self.conn.execute(
                insert(merchant_cache).values(
                    merchant_name=merchant, category=category, source="test"
                )
            )
        self.conn.commit()
        return txn_id


@pytest.fixture
def seeder(conn, account):
    return TxnSeeder(conn, account)
