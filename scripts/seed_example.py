#!/usr/bin/env python3
"""Seed an example household into the database for demo / preview environments.

Idempotent: if the example snapshot already exists it does nothing, so it is
safe to run on every container boot. Intended for throwaway PR-preview
instances (see the "PR preview environments" section of the README) and for
kicking the tyres locally:

    FINTRACK_DB=preview.db uv run python scripts/seed_example.py

It only ever writes the example snapshot; it never touches other snapshots.
"""

import hashlib
import os
from calendar import monthrange
from datetime import date
from decimal import Decimal

from fintrack.accounts.repository import add_account
from fintrack.budget.repository import add_budget_entry
from fintrack.core.db import get_engine, init_db
from fintrack.ledger.repository.imports import (
    confirm_import,
    create_import,
    insert_transactions,
)
from fintrack.ledger.repository.merchants import set_merchant_category
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
            "category": "Housing",
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
            "category": "Groceries",
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

    # --- Transactions (several months of realistic spending) ------------------
    # Spread transactions across the last 4 complete months so the Trends page
    # has data in every column and the Budget/mo + Delta columns are populated.
    _seed_transactions(conn, checking, today)

    print(f"Seeded example snapshot {SNAPSHOT_NAME!r} (id={sid}).")
    return True


def _fingerprint(
    account_id: int, txn_date: date, amount: str, desc: str, seq: int = 0
) -> str:
    """Deterministic fingerprint matching the importer's dedup scheme."""
    key = f"{txn_date.isoformat()}|{amount}|{desc}|{account_id}|{seq}"
    return hashlib.sha256(key.encode()).hexdigest()


def _seed_transactions(conn, checking_id: int, today: date) -> None:
    """Insert 4 months of categorized transactions into the checking account."""

    # Merchant → category mappings (seeded into merchant_cache so Trends resolves them).
    merchant_categories = {
        "Whole Foods Market": "Groceries",
        "Trader Joes": "Groceries",
        "Safeway": "Groceries",
        "Chipotle": "Dining",
        "Starbucks": "Dining",
        "Shell Oil": "Transport",
        "Uber": "Transport",
        "Landlord Properties LLC": "Housing",
        "Electric Utility Co": "Utilities",
        "Netflix": "Subscriptions",
        "Spotify": "Subscriptions",
        "Amazon": "Shopping",
        "Target": "Shopping",
        "Employer Direct Deposit": "Income",
    }
    for merchant, category in merchant_categories.items():
        set_merchant_category(conn, merchant, category, source="seed")

    # Build 4 months of transactions.  Start on the 1st of 4 months ago so
    # every month is complete (the current partial month is excluded from the
    # trailing-average in the Trends Avg/mo column).
    first_of_current = today.replace(day=1)
    months_back = 4
    month_starts = []
    for i in range(months_back, 0, -1):
        # Walk backwards i months from the 1st of the current month
        m = first_of_current.month - i
        y = first_of_current.year
        while m < 1:
            m += 12
            y -= 1
        month_starts.append(date(y, m, 1))

    # Recurring monthly transactions (appear every month)
    monthly_txns = [
        # (day-of-month, amount, merchant, raw_description)
        (1, "5200.00", "Employer Direct Deposit", "DIRECT DEP EMPLOYER PAYROLL"),
        (1, "-1800.00", "Landlord Properties LLC", "ONLINE PMT LANDLORD PROPERTIES"),
        (3, "-125.50", "Whole Foods Market", "WHOLE FOODS MKT #10847"),
        (5, "-6.50", "Starbucks", "STARBUCKS STORE #4821"),
        (8, "-42.00", "Shell Oil", "SHELL SERVICE STATION"),
        (10, "-89.95", "Safeway", "SAFEWAY STORE #3127"),
        (12, "-15.99", "Netflix", "NETFLIX.COM"),
        (14, "-11.99", "Spotify", "SPOTIFY USA"),
        (15, "-68.00", "Trader Joes", "TRADER JOE'S #192"),
        (18, "-14.50", "Chipotle", "CHIPOTLE ONLINE ORD"),
        (20, "-95.00", "Electric Utility Co", "ELECTRIC UTILITY CO AUTOPAY"),
        (22, "-35.00", "Uber", "UBER *TRIP"),
        (25, "-48.75", "Target", "TARGET T-2847"),
    ]

    # A few per-month variations to make the data less uniform
    extras_by_month = [
        # month 0 (oldest): a big Amazon order
        [
            (7, "-189.99", "Amazon", "AMZN MKTP US*RT4K29Z"),
        ],
        # month 1: grocery run + extra dining
        [
            (16, "-72.40", "Whole Foods Market", "WHOLE FOODS MKT #10847"),
            (19, "-32.00", "Chipotle", "CHIPOTLE ONLINE ORD"),
        ],
        # month 2: another Amazon purchase
        [
            (9, "-64.50", "Amazon", "AMZN MKTP US*AH2M41P"),
        ],
        # month 3 (most recent): extra groceries
        [
            (11, "-55.20", "Safeway", "SAFEWAY STORE #3127"),
            (23, "-28.00", "Uber", "UBER *TRIP"),
        ],
    ]

    # Create a confirmed import and insert all transactions
    import_id = create_import(
        conn,
        account_id=checking_id,
        filename="seed_example_transactions.ofx",
        file_hash=hashlib.sha256(b"seed-example-preview").hexdigest(),
    )

    all_txns = []
    for month_idx, month_start in enumerate(month_starts):
        txns_for_month = monthly_txns + extras_by_month[month_idx]
        for day, amount, merchant, raw_desc in txns_for_month:
            # Clamp day to valid range for this month
            _, last_day = monthrange(month_start.year, month_start.month)
            txn_date = date(month_start.year, month_start.month, min(day, last_day))
            fp = _fingerprint(checking_id, txn_date, amount, raw_desc)
            all_txns.append(
                {
                    "date": txn_date,
                    "amount": Decimal(amount),
                    "raw_description": raw_desc,
                    "normalized_merchant": merchant,
                    "fingerprint": fp,
                }
            )

    insert_transactions(
        conn, import_id=import_id, account_id=checking_id, transactions_data=all_txns
    )
    confirm_import(conn, import_id)


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
