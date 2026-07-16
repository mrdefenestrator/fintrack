"""Type definitions for finances entities using TypedDict."""

from decimal import Decimal
from typing import Literal, TypedDict

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
    balance: Decimal  # For non-credit_card accounts
    limit: Decimal  # For credit_card accounts
    available: Decimal  # For credit_card accounts
    rewards_balance: Decimal  # Optional for credit_card
    statement_balance: Decimal  # Optional for credit_card
    statement_due_day_of_month: int  # Optional for credit_card (1-31)
    paymentAccountRef: int  # Account id for CC autopay source
    asOfDate: str  # ISO8601 date string
    minimum_balance: Decimal  # Target floor balance
    institution: str  # Bank/provider name
    partial_account_number: str  # Last 4 digits etc.


class BudgetEntry(TypedDict, total=False):
    """Unified budget entry (income or expense) in finances data."""

    kind: Literal["income", "expense"]  # Required - income or expense
    description: str  # Required - label
    amount: Decimal  # Required - amount
    recurrence: Recurrence  # Required - recurrence type
    type: str  # Optional category (income or expense type)
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
