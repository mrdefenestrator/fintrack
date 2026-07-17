"""Type definitions shared by the ledger and net-worth domains.

Dict field names (e.g. "limit", "type", "asOfDate") are the external API used
by the CLI, web routes, and templates; they are mapped to unified column names
(credit_limit, account_type, as_of_date) at the repository boundary.
"""

from datetime import date
from decimal import Decimal
from typing import Literal, TypedDict

# ---------------------------------------------------------------------------
# Net-worth / budget domain (from finances)
# ---------------------------------------------------------------------------

# Account types enum
AccountType = Literal[
    "credit_card",
    "checking",
    "savings",
    "gift_card",
    "wallet",
    "digital_wallet",
    "loan",
    "other",
]

# Income types enum
IncomeType = Literal["salary", "refund", "bonus", "remittance"]

# Expense types enum
ExpenseType = Literal[
    "housing", "insurance", "service", "utility", "product", "transport", "food"
]

# Recurrence types enum
Recurrence = Literal[
    "one_time", "monthly", "biweekly", "quarterly", "semiannual", "annual"
]


class Account(TypedDict, total=False):
    """Account entry in finances data."""

    id: int  # Required - unique identifier
    name: str  # Required - display name
    type: AccountType  # Required - account type
    balance: Decimal  # Canonical signed balance (negative = owed on CCs)
    limit: Decimal  # For credit_card accounts (credit_limit column)
    available: Decimal  # For credit_card accounts
    rewards_balance: Decimal  # Optional for credit_card
    statement_balance: Decimal  # Optional for credit_card
    statement_due_day_of_month: int  # Optional for credit_card (1-31)
    paymentAccountRef: int  # Account id for CC autopay source
    asOfDate: str  # ISO8601 date string
    minimum_balance: Decimal  # Target floor balance
    institution: str  # Bank/provider name


class BudgetEntry(TypedDict, total=False):
    """Unified budget entry (income or expense) in finances data."""

    kind: Literal["income", "expense"]  # Required - income or expense
    description: str  # Required - label
    amount: Decimal  # Required - amount
    recurrence: Recurrence  # Required - recurrence type
    type: str  # Optional category (income or expense type)
    category: str  # Optional ledger category this entry covers
    date: str  # For one_time - ISO8601
    dayOfMonth: int  # For monthly/quarterly/semiannual (1-31)
    month: int  # For quarterly/semiannual/annual (1-12)
    dayOfYear: int  # For annual (1-31)
    continuous: bool  # If True, prorate for budget
    autoAccountRef: int  # Account id for deposit/payment


class AssetEntry(TypedDict, total=False):
    """Unified asset/debt entry in finances data."""

    kind: Literal["asset", "debt"]  # Required - asset or debt
    name: str  # Required - display name
    id: int  # For asset entries - unique identifier; referenced by debt assetRef
    institution: str  # Optional provider/lender name
    # Asset-only fields
    value: Decimal  # Estimated value per unit
    source: str  # Optional valuation source
    # Shared (asset + debt) fields
    quantity: Decimal  # Optional; default 1. Assets: value × quantity. Debts: balance × quantity
    # Debt-only fields
    balance: Decimal  # Amount owed per unit
    assetRef: int  # Optional link to asset entry id
    interestRate: Decimal  # Optional annual rate as decimal
    nextDueDate: str  # Optional ISO8601 date
    asOfDate: str  # Optional ISO8601 date


class FinancesData(TypedDict, total=False):
    """Top-level finances data structure."""

    accounts: list[Account]
    budget: list[BudgetEntry]
    assets: list[AssetEntry]


# ---------------------------------------------------------------------------
# Ledger domain (from spending)
# ---------------------------------------------------------------------------


class ParsedTransaction(TypedDict):
    date: date
    amount: Decimal
    raw_description: str


class _ImportResultRequired(TypedDict):
    transactions: list[ParsedTransaction]
    account_name: str | None


class ImportResult(_ImportResultRequired, total=False):
    ledger_balance: Decimal | None
    ledger_balance_date: date | None
    available_balance: Decimal | None
    available_balance_date: date | None
    beginning_balance: Decimal | None


class AccountMeta(TypedDict):
    institution: str  # e.g. "Chase" (empty string if unavailable)
    account_type: str  # "checking" | "savings" | "credit_card" | "other"
    suggested_name: str  # e.g. "Chase Checking ...7890"
