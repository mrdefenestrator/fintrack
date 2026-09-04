#!/usr/bin/env python3
"""Seed demo households into the database for demo / preview / dev environments.

Seeds three throwaway snapshots that exist to exercise the UI at three very
different data densities:

  - **Dense Household**  — many accounts, cards, loans, assets, budget lines and
    several months of transactions; deliberately overflows every sheet so we can
    check scrolling, sticky rows/footers and dense-table rendering.
  - **Sparse Household** — a handful of records (one of most things); the
    minimal-but-nonempty case.
  - **Empty Household**  — a snapshot with no data at all; the empty-state case.

Idempotent per snapshot: each is created only if a snapshot of that name does
not already exist, so it is safe to run on every container boot. Intended for
throwaway PR-preview instances (see the "PR preview environments" section of the
README) and for kicking the tyres locally:

    FINTRACK_DB=preview.db uv run python scripts/seed_example.py

It only ever writes these demo snapshots; it never touches other snapshots.
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

DENSE_SNAPSHOT = "Dense Household"
SPARSE_SNAPSHOT = "Sparse Household"
EMPTY_SNAPSHOT = "Empty Household"

# The demo households, densest first, so the app has something to show on first
# load and every render density has a fixture.
SNAPSHOT_NAMES = [DENSE_SNAPSHOT, SPARSE_SNAPSHOT, EMPTY_SNAPSHOT]


# ---------------------------------------------------------------------------
# Dense household — deliberately overflows every sheet
# ---------------------------------------------------------------------------
def seed_dense(conn) -> bool:
    """Create the dense demo snapshot. Returns True if it seeded, False if it
    already existed (and was left untouched)."""
    if get_snapshot_id(conn, DENSE_SNAPSHOT) is not None:
        print(f"Seed snapshot {DENSE_SNAPSHOT!r} already present; skipping.")
        return False

    sid = create_snapshot(conn, DENSE_SNAPSHOT)
    today = date.today()
    iso = today.isoformat()

    # --- Cash accounts -------------------------------------------------------
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
            "name": "Joint Checking",
            "type": "checking",
            "institution": "Anytown Bank",
            "balance": Decimal("2875.30"),
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
            "name": "Vacation Savings",
            "type": "savings",
            "institution": "Anytown Bank",
            "balance": Decimal("3200.00"),
            "asOfDate": iso,
        },
        today=today,
    )
    add_account(
        conn,
        sid,
        {
            "name": "Home Down Payment",
            "type": "savings",
            "institution": "Marcus",
            "balance": Decimal("22500.00"),
            "minimum_balance": Decimal("20000.00"),
            "asOfDate": iso,
        },
        today=today,
    )
    add_account(
        conn,
        sid,
        {
            "name": "Cash on Hand",
            "type": "wallet",
            "balance": Decimal("340.00"),
            "asOfDate": iso,
        },
        today=today,
    )
    add_account(
        conn,
        sid,
        {
            "name": "Venmo Balance",
            "type": "digital_wallet",
            "institution": "PayPal",
            "balance": Decimal("128.44"),
            "asOfDate": iso,
        },
        today=today,
    )
    add_account(
        conn,
        sid,
        {
            "name": "Amazon Gift Card",
            "type": "gift_card",
            "institution": "Amazon",
            "balance": Decimal("75.00"),
            "asOfDate": iso,
        },
        today=today,
    )

    # --- Credit cards (all autopay from Everyday Checking) -------------------
    credit_cards = [
        ("Travel Rewards Card", "Big Card Co", "-842.17", "8000.00", 15),
        ("Cashback Everyday", "Big Card Co", "-312.90", "12000.00", 3),
        ("Grocery Rewards", "Anytown Bank", "-128.45", "5000.00", 20),
        ("Warehouse Store Card", "Warehouse Club", "-450.00", "3500.00", 10),
        ("Airline Miles Card", "Sky Bank", "-1580.22", "15000.00", 25),
    ]
    for name, institution, balance, limit, due in credit_cards:
        add_account(
            conn,
            sid,
            {
                "name": name,
                "type": "credit_card",
                "institution": institution,
                # Canonical signed balance: negative == owed.
                "balance": Decimal(balance),
                "limit": Decimal(limit),
                "statement_due_day_of_month": due,
                "paymentAccountRef": checking,
                "asOfDate": iso,
            },
            today=today,
        )

    # --- Assets (needed before the loans that secure them) ------------------
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
    add_asset_entry(
        conn,
        sid,
        {
            "kind": "asset",
            "type": "retirement",
            "name": "Roth IRA",
            "institution": "Vanguard",
            "value": Decimal("34500.00"),
            "annualReturnRate": Decimal("0.07"),
            "monthlyContribution": Decimal("500.00"),
        },
    )
    add_asset_entry(
        conn,
        sid,
        {
            "kind": "asset",
            "type": "hsa",
            "name": "Health Savings Account",
            "institution": "Optum Bank",
            "value": Decimal("6800.00"),
            "annualReturnRate": Decimal("0.05"),
            "monthlyContribution": Decimal("200.00"),
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
            "kind": "asset",
            "type": "real_estate",
            "name": "Rental Property",
            "institution": "Anytown",
            "value": Decimal("285000.00"),
            "annualReturnRate": Decimal("0.03"),
        },
    )
    car_id = add_asset_entry(
        conn,
        sid,
        {
            "kind": "asset",
            "type": "vehicle",
            "name": "2022 Sedan",
            "value": Decimal("24000.00"),
            "annualReturnRate": Decimal("-0.10"),
        },
    )
    add_asset_entry(
        conn,
        sid,
        {
            "kind": "asset",
            "type": "vehicle",
            "name": "Family SUV",
            "value": Decimal("38000.00"),
            "annualReturnRate": Decimal("-0.10"),
        },
    )

    # Brokerage / crypto holdings (prices fetched from external APIs at render).
    brokerage = [
        ("Bitcoin", "Coinbase", "BTC", "0.45", "60000.00"),
        ("Ethereum", "Coinbase", "ETH", "3.2", "3500.00"),
        ("Apple Inc", "Schwab", "AAPL", "50", "175.00"),
        ("Microsoft", "Schwab", "MSFT", "30", "415.00"),
        ("NVIDIA", "Schwab", "NVDA", "15", "128.00"),
        ("Vanguard S&P 500 ETF", "Schwab", "VOO", "25", "480.00"),
        ("Vanguard Total Bond ETF", "Schwab", "BND", "100", "72.00"),
    ]
    for name, institution, unit, quantity, value in brokerage:
        add_asset_entry(
            conn,
            sid,
            {
                "kind": "asset",
                "type": "brokerage",
                "name": name,
                "institution": institution,
                "unit": unit,
                "quantity": Decimal(quantity),
                "value": Decimal(value),
            },
        )

    # --- Loans (debt entries; the mortgage/auto loan secure their assets) ---
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
    add_asset_entry(
        conn,
        sid,
        {
            "kind": "debt",
            "type": "loan",
            "name": "Auto Loan",
            "institution": "Anytown Bank",
            "balance": Decimal("18450.00"),
            "interestRate": Decimal("0.049"),
            "originalPrincipal": Decimal("32000.00"),
            "termMonths": 60,
            "originationDate": "2023-03-15",
            "statement_due_day_of_month": 5,
            "assetRef": car_id,
        },
    )
    add_asset_entry(
        conn,
        sid,
        {
            "kind": "debt",
            "type": "loan",
            "name": "Student Loan",
            "institution": "Federal Servicer",
            "balance": Decimal("22300.00"),
            "interestRate": Decimal("0.055"),
            "originalPrincipal": Decimal("45000.00"),
            "termMonths": 120,
            "originationDate": "2016-09-01",
            "statement_due_day_of_month": 21,
        },
    )
    add_asset_entry(
        conn,
        sid,
        {
            "kind": "debt",
            "type": "loan",
            "name": "Personal Loan",
            "institution": "Lending Co",
            "balance": Decimal("4800.00"),
            "interestRate": Decimal("0.099"),
            "originalPrincipal": Decimal("10000.00"),
            "termMonths": 36,
            "originationDate": "2024-11-01",
            "statement_due_day_of_month": 12,
        },
    )

    # --- Budget (recurring income + a wide spread of expenses) --------------
    budget_entries = [
        {
            "kind": "income",
            "description": "Salary",
            "amount": "5200.00",
            "recurrence": "monthly",
            "dayOfMonth": 1,
            "category": "Income",
        },
        {
            "kind": "income",
            "description": "Partner Salary",
            "amount": "3800.00",
            "recurrence": "monthly",
            "dayOfMonth": 1,
            "category": "Income",
        },
        {
            "kind": "income",
            "description": "Rental Income",
            "amount": "1650.00",
            "recurrence": "monthly",
            "dayOfMonth": 5,
            "category": "Income",
        },
        {
            "kind": "income",
            "description": "Brokerage Dividends",
            "amount": "450.00",
            "recurrence": "quarterly",
            "dayOfMonth": 15,
            "month": 3,
            "category": "Income",
        },
        {
            "kind": "expense",
            "description": "Rent",
            "amount": "1800.00",
            "recurrence": "monthly",
            "dayOfMonth": 1,
            "category": "Housing",
        },
        {
            "kind": "expense",
            "description": "Groceries",
            "amount": "600.00",
            "recurrence": "monthly",
            "continuous": True,
            "category": "Groceries",
        },
        {
            "kind": "expense",
            "description": "Dining Out",
            "amount": "350.00",
            "recurrence": "monthly",
            "continuous": True,
            "category": "Dining",
        },
        {
            "kind": "expense",
            "description": "Gas & Fuel",
            "amount": "180.00",
            "recurrence": "monthly",
            "continuous": True,
            "category": "Transport",
        },
        {
            "kind": "expense",
            "description": "Electric",
            "amount": "95.00",
            "recurrence": "monthly",
            "dayOfMonth": 20,
            "category": "Utilities",
        },
        {
            "kind": "expense",
            "description": "Water & Sewer",
            "amount": "55.00",
            "recurrence": "monthly",
            "dayOfMonth": 18,
            "category": "Utilities",
        },
        {
            "kind": "expense",
            "description": "Internet",
            "amount": "75.00",
            "recurrence": "monthly",
            "dayOfMonth": 8,
            "category": "Utilities",
        },
        {
            "kind": "expense",
            "description": "Cell Phone",
            "amount": "120.00",
            "recurrence": "monthly",
            "dayOfMonth": 12,
            "category": "Utilities",
        },
        {
            "kind": "expense",
            "description": "Netflix",
            "amount": "15.99",
            "recurrence": "monthly",
            "dayOfMonth": 12,
            "category": "Subscriptions",
        },
        {
            "kind": "expense",
            "description": "Spotify",
            "amount": "11.99",
            "recurrence": "monthly",
            "dayOfMonth": 14,
            "category": "Subscriptions",
        },
        {
            "kind": "expense",
            "description": "Gym Membership",
            "amount": "45.00",
            "recurrence": "monthly",
            "dayOfMonth": 2,
            "category": "Healthcare",
        },
        {
            "kind": "expense",
            "description": "Auto Insurance",
            "amount": "145.00",
            "recurrence": "monthly",
            "dayOfMonth": 6,
            "category": "Insurance",
        },
        {
            "kind": "expense",
            "description": "Health Insurance",
            "amount": "320.00",
            "recurrence": "monthly",
            "dayOfMonth": 1,
            "category": "Insurance",
        },
        {
            "kind": "expense",
            "description": "Childcare",
            "amount": "1100.00",
            "recurrence": "monthly",
            "dayOfMonth": 1,
            "category": "Other",
        },
        {
            "kind": "expense",
            "description": "Property Tax",
            "amount": "6400.00",
            "recurrence": "annual",
            "dayOfMonth": 15,
            "month": 11,
            "category": "Taxes",
        },
        {
            "kind": "expense",
            "description": "Amazon Prime",
            "amount": "139.00",
            "recurrence": "annual",
            "dayOfMonth": 3,
            "month": 7,
            "category": "Subscriptions",
        },
    ]
    for entry in budget_entries:
        entry = dict(entry)
        entry["amount"] = Decimal(entry["amount"])
        add_budget_entry(conn, sid, entry)

    # --- Transactions (several months of realistic spending) ----------------
    _seed_dense_transactions(conn, checking, today)

    print(f"Seeded example snapshot {DENSE_SNAPSHOT!r} (id={sid}).")
    return True


# ---------------------------------------------------------------------------
# Sparse household — a handful of records; the minimal-but-nonempty case
# ---------------------------------------------------------------------------
def seed_sparse(conn) -> bool:
    """Create the sparse demo snapshot. Returns True if it seeded, False if it
    already existed (and was left untouched)."""
    if get_snapshot_id(conn, SPARSE_SNAPSHOT) is not None:
        print(f"Seed snapshot {SPARSE_SNAPSHOT!r} already present; skipping.")
        return False

    sid = create_snapshot(conn, SPARSE_SNAPSHOT)
    today = date.today()
    iso = today.isoformat()

    checking = add_account(
        conn,
        sid,
        {
            "name": "Checking",
            "type": "checking",
            "institution": "Anytown Bank",
            "balance": Decimal("1200.00"),
            "asOfDate": iso,
        },
        today=today,
    )
    add_account(
        conn,
        sid,
        {
            "name": "Credit Card",
            "type": "credit_card",
            "institution": "Big Card Co",
            "balance": Decimal("-95.00"),
            "limit": Decimal("2000.00"),
            "statement_due_day_of_month": 15,
            "paymentAccountRef": checking,
            "asOfDate": iso,
        },
        today=today,
    )
    add_budget_entry(
        conn,
        sid,
        {
            "kind": "income",
            "description": "Paycheck",
            "amount": Decimal("2500.00"),
            "recurrence": "monthly",
            "dayOfMonth": 1,
            "category": "Income",
        },
    )
    add_asset_entry(
        conn,
        sid,
        {
            "kind": "asset",
            "type": "retirement",
            "name": "IRA",
            "institution": "Vanguard",
            "value": Decimal("12000.00"),
            "annualReturnRate": Decimal("0.07"),
        },
    )

    # A couple of transactions so the ledger/trends pages aren't empty either.
    set_merchant_category(conn, "Corner Store", "Groceries", source="seed")
    import_id = create_import(
        conn,
        account_id=checking,
        filename="seed_sparse_transactions.ofx",
        file_hash=hashlib.sha256(b"seed-sparse-preview").hexdigest(),
    )
    first = today.replace(day=1)
    txns = [
        (first, "-24.15", "Corner Store", "CORNER STORE #12"),
        (first, "-8.40", "Corner Store", "CORNER STORE #12"),
    ]
    all_txns = []
    for txn_date, amount, merchant, raw_desc in txns:
        all_txns.append(
            {
                "date": txn_date,
                "amount": Decimal(amount),
                "raw_description": raw_desc,
                "normalized_merchant": merchant,
                "fingerprint": _fingerprint(checking, txn_date, amount, raw_desc),
            }
        )
    insert_transactions(
        conn, import_id=import_id, account_id=checking, transactions_data=all_txns
    )
    confirm_import(conn, import_id)

    print(f"Seeded example snapshot {SPARSE_SNAPSHOT!r} (id={sid}).")
    return True


# ---------------------------------------------------------------------------
# Empty household — a snapshot with no data at all
# ---------------------------------------------------------------------------
def seed_empty(conn) -> bool:
    """Create the empty demo snapshot (no data). Returns True if it seeded,
    False if it already existed."""
    if get_snapshot_id(conn, EMPTY_SNAPSHOT) is not None:
        print(f"Seed snapshot {EMPTY_SNAPSHOT!r} already present; skipping.")
        return False
    sid = create_snapshot(conn, EMPTY_SNAPSHOT)
    print(f"Seeded example snapshot {EMPTY_SNAPSHOT!r} (id={sid}).")
    return True


def _fingerprint(
    account_id: int, txn_date: date, amount: str, desc: str, seq: int = 0
) -> str:
    """Deterministic fingerprint matching the importer's dedup scheme."""
    key = f"{txn_date.isoformat()}|{amount}|{desc}|{account_id}|{seq}"
    return hashlib.sha256(key.encode()).hexdigest()


def _seed_dense_transactions(conn, checking_id: int, today: date) -> None:
    """Insert several months of categorized transactions into the checking
    account, spread wide enough to overflow the Transactions and Trends pages."""

    # Merchant → category mappings (seeded into merchant_cache so Trends resolves them).
    merchant_categories = {
        "Whole Foods Market": "Groceries",
        "Trader Joes": "Groceries",
        "Safeway": "Groceries",
        "Costco Wholesale": "Groceries",
        "Chipotle": "Dining",
        "Starbucks": "Dining",
        "Panera Bread": "Dining",
        "Local Thai Kitchen": "Dining",
        "Shell Oil": "Transport",
        "Chevron": "Transport",
        "Uber": "Transport",
        "Lyft": "Transport",
        "Landlord Properties LLC": "Housing",
        "Electric Utility Co": "Utilities",
        "City Water Dept": "Utilities",
        "Netflix": "Subscriptions",
        "Spotify": "Subscriptions",
        "Disney Plus": "Subscriptions",
        "Amazon": "Shopping",
        "Target": "Shopping",
        "Best Buy": "Shopping",
        "CVS Pharmacy": "Healthcare",
        "Delta Air Lines": "Travel",
        "Employer Direct Deposit": "Income",
    }
    for merchant, category in merchant_categories.items():
        set_merchant_category(conn, merchant, category, source="seed")

    # Build several months of transactions. Start on the 1st of N months ago so
    # every month is complete (the current partial month is excluded from the
    # trailing-average in the Trends Avg/mo column).
    first_of_current = today.replace(day=1)
    months_back = 6
    month_starts = []
    for i in range(months_back, 0, -1):
        # Walk backwards i months from the 1st of the current month
        m = first_of_current.month - i
        y = first_of_current.year
        while m < 1:
            m += 12
            y -= 1
        month_starts.append(date(y, m, 1))

    # Recurring monthly transactions (appear every month).
    monthly_txns = [
        # (day-of-month, amount, merchant, raw_description)
        (1, "5200.00", "Employer Direct Deposit", "DIRECT DEP EMPLOYER PAYROLL"),
        (1, "-1800.00", "Landlord Properties LLC", "ONLINE PMT LANDLORD PROPERTIES"),
        (2, "-52.30", "Costco Wholesale", "COSTCO WHSE #0417"),
        (3, "-125.50", "Whole Foods Market", "WHOLE FOODS MKT #10847"),
        (4, "-9.75", "Panera Bread", "PANERA BREAD #601"),
        (5, "-6.50", "Starbucks", "STARBUCKS STORE #4821"),
        (7, "-58.00", "Chevron", "CHEVRON 00291"),
        (8, "-42.00", "Shell Oil", "SHELL SERVICE STATION"),
        (10, "-89.95", "Safeway", "SAFEWAY STORE #3127"),
        (11, "-22.40", "CVS Pharmacy", "CVS/PHARMACY #8842"),
        (12, "-15.99", "Netflix", "NETFLIX.COM"),
        (13, "-7.99", "Disney Plus", "DISNEY PLUS"),
        (14, "-11.99", "Spotify", "SPOTIFY USA"),
        (15, "-68.00", "Trader Joes", "TRADER JOE'S #192"),
        (17, "-31.20", "Local Thai Kitchen", "LOCAL THAI KITCHEN"),
        (18, "-14.50", "Chipotle", "CHIPOTLE ONLINE ORD"),
        (19, "-18.75", "Lyft", "LYFT *RIDE"),
        (20, "-95.00", "Electric Utility Co", "ELECTRIC UTILITY CO AUTOPAY"),
        (21, "-46.10", "City Water Dept", "CITY WATER DEPT AUTOPAY"),
        (22, "-35.00", "Uber", "UBER *TRIP"),
        (25, "-48.75", "Target", "TARGET T-2847"),
    ]

    # Per-month variations to make the data less uniform.
    extras_by_month = [
        # month 0 (oldest): a big Amazon order + electronics
        [
            (7, "-189.99", "Amazon", "AMZN MKTP US*RT4K29Z"),
            (24, "-329.00", "Best Buy", "BEST BUY #1187"),
        ],
        # month 1: grocery run + extra dining
        [
            (16, "-72.40", "Whole Foods Market", "WHOLE FOODS MKT #10847"),
            (19, "-32.00", "Chipotle", "CHIPOTLE ONLINE ORD"),
        ],
        # month 2: a flight
        [
            (9, "-64.50", "Amazon", "AMZN MKTP US*AH2M41P"),
            (14, "-412.60", "Delta Air Lines", "DELTA AIR LINES"),
        ],
        # month 3: extra groceries + rideshare
        [
            (11, "-55.20", "Safeway", "SAFEWAY STORE #3127"),
            (23, "-28.00", "Uber", "UBER *TRIP"),
        ],
        # month 4: pharmacy + coffee run
        [
            (6, "-63.80", "CVS Pharmacy", "CVS/PHARMACY #8842"),
            (21, "-18.25", "Starbucks", "STARBUCKS STORE #4821"),
        ],
        # month 5 (most recent complete): warehouse haul + dining
        [
            (13, "-142.15", "Costco Wholesale", "COSTCO WHSE #0417"),
            (27, "-44.90", "Panera Bread", "PANERA BREAD #601"),
        ],
    ]

    # Create a confirmed import and insert all transactions.
    import_id = create_import(
        conn,
        account_id=checking_id,
        filename="seed_dense_transactions.ofx",
        file_hash=hashlib.sha256(b"seed-dense-preview").hexdigest(),
    )

    all_txns = []
    for month_idx, month_start in enumerate(month_starts):
        txns_for_month = monthly_txns + extras_by_month[month_idx]
        for day, amount, merchant, raw_desc in txns_for_month:
            # Clamp day to valid range for this month.
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
        seed_dense(conn)
        seed_sparse(conn)
        seed_empty(conn)


if __name__ == "__main__":
    main()
