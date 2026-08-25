"""Fixtures for projection tests: in-memory DB with one snapshot, plus a
ledger seeding helper for estimator tests."""

import uuid
from datetime import date
from typing import Iterable

import pytest
from sqlalchemy import create_engine, insert

from sqlalchemy import select

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
    yield engine
    engine.dispose()


@pytest.fixture
def conn(engine):
    with engine.connect() as connection:
        yield connection


@pytest.fixture
def snapshot_id(conn):
    return create_snapshot(conn, "test")


def seed_transactions(
    conn,
    account_id: int,
    rows: Iterable[tuple[date, str, str, str]],
) -> None:
    """Insert confirmed ledger transactions: (date, amount, merchant, category)."""
    holding_group = conn.execute(
        select(holdings.c.group_key).where(holdings.c.id == account_id)
    ).scalar()
    import_id = conn.execute(
        insert(imports).values(
            account_id=account_id,
            holding_group=holding_group,
            filename="seed.ofx",
            file_hash=uuid.uuid4().hex,
            status="confirmed",
        )
    ).inserted_primary_key[0]
    seen_merchants = set()
    for txn_date, amount, merchant, category in rows:
        conn.execute(
            insert(transactions).values(
                import_id=import_id,
                account_id=account_id,
                date=txn_date,
                amount=amount,
                raw_description=merchant,
                normalized_merchant=merchant,
                fingerprint=uuid.uuid4().hex,
            )
        )
        if merchant not in seen_merchants:
            seen_merchants.add(merchant)
            conn.execute(
                insert(merchant_cache).values(
                    merchant_name=merchant, category=category, source="test"
                )
            )
    conn.commit()
