#!/usr/bin/env python3
"""Seed an example household into the database for demo / preview environments.

Idempotent: if the example snapshot already exists it does nothing, so it is
safe to run on every container boot. Intended for throwaway PR-preview
instances (see the "PR preview environments" section of the README) and for
kicking the tyres locally:

    FINTRACK_DB=preview.db uv run python scripts/seed_example.py

It only ever writes the example snapshot; it never touches other snapshots.
"""

import os
from datetime import date
from decimal import Decimal

from fintrack.accounts.repository import add_account
from fintrack.budget.repository import add_budget_entry
from fintrack.core.db import get_engine, init_db
from fintrack.networth.repository import add_asset_entry
from fintrack.snapshots.repository import create_snapshot, get_snapshot_id

SNAPSHOT_NAME = "Example Household"


def seed(conn) -> bool:
    """Create the example snapshot. Returns True if it seeded, False if it
    already existed (and was left untouched)."""
    if get_snapshot_id(conn, SNAPSHOT_NAME) is not None:
        print(f"Seed snapshot {SNAPSHOT_NAME!r} already present; skipping.")
        return False

    sid = create_snapshot(conn, SNAPSHOT_NAME)
    today = date.today()
    iso = today.isoformat()

    # --- Accounts (cash + a credit card that autopays from checking) ---------
    checking = add_account(
        conn,
        sid,
        {
            "name": "Everyday Checking",
            "type": "checking",
            "institution": "Anytown Bank",
            "balance": Decimal("4210.55"),
            "asOfDate": iso,
        },
        today=today,
    )
    add_account(
        conn,
        sid,
        {
            "name": "Emergency Fund",
            "type": "savings",
            "institution": "Anytown Bank",
            "balance": Decimal("15000.00"),
            "minimum_balance": Decimal("10000.00"),
            "asOfDate": iso,
        },
        today=today,
    )
    add_account(
        conn,
        sid,
        {
            "name": "Travel Rewards Card",
            "type": "credit_card",
            "institution": "Big Card Co",
            # Canonical signed balance: negative == owed.
            "balance": Decimal("-842.17"),
            "limit": Decimal("8000.00"),
            "statement_due_day_of_month": 15,
            "paymentAccountRef": checking,
            "asOfDate": iso,
        },
        today=today,
    )

    # --- Budget (recurring income + expenses) --------------------------------
    add_budget_entry(
        conn,
        sid,
        {
            "kind": "income",
            "description": "Salary",
            "amount": Decimal("5200.00"),
            "recurrence": "monthly",
            "dayOfMonth": 1,
        },
    )
    add_budget_entry(
        conn,
        sid,
        {
            "kind": "expense",
            "description": "Rent",
            "amount": Decimal("1800.00"),
            "recurrence": "monthly",
            "dayOfMonth": 1,
            "category": "housing",
        },
    )
    add_budget_entry(
        conn,
        sid,
        {
            "kind": "expense",
            "description": "Groceries",
            "amount": Decimal("600.00"),
            "recurrence": "monthly",
            "continuous": True,
            "category": "food",
        },
    )

    # --- Assets & debts (a home + its mortgage, plus retirement) -------------
    add_asset_entry(
        conn,
        sid,
        {
            "kind": "asset",
            "type": "retirement",
            "name": "401(k)",
            "institution": "Fidelity",
            "value": Decimal("82000.00"),
            "annualReturnRate": Decimal("0.07"),
            "monthlyContribution": Decimal("500.00"),
        },
    )
    home_id = add_asset_entry(
        conn,
        sid,
        {
            "kind": "asset",
            "type": "real_estate",
            "name": "Primary Residence",
            "value": Decimal("420000.00"),
            "annualReturnRate": Decimal("0.03"),
        },
    )
    add_asset_entry(
        conn,
        sid,
        {
            "kind": "debt",
            "type": "loan",
            "name": "Mortgage",
            "institution": "Home Loan Bank",
            "balance": Decimal("268000.00"),
            "interestRate": Decimal("0.0625"),
            "originalPrincipal": Decimal("340000.00"),
            "termMonths": 360,
            "originationDate": "2019-06-01",
            "statement_due_day_of_month": 1,
            "assetRef": home_id,
        },
    )

    print(f"Seeded example snapshot {SNAPSHOT_NAME!r} (id={sid}).")
    return True


def main() -> None:
    db_path = os.environ.get("FINTRACK_DB", "fintrack.db")
    engine = get_engine(db_path)
    # Safe no-op when the schema already exists (e.g. after `alembic upgrade
    # head` in the container entrypoint); creates it when run standalone.
    init_db(engine)
    with engine.connect() as conn:
        seed(conn)


if __name__ == "__main__":
    main()
