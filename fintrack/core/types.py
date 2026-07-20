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

# Canonical, ordered (value, display label) list of account types. This is
# the single source of truth for every account-type select/validation in the
# app (import quick-create, accounts page type editor, CLI --type choices).
# Must stay a superset of every account_type value that appears in the DB.
ACCOUNT_TYPE_OPTIONS: list[tuple[str, str]] = [
    ("checking", "Checking"),
    ("savings", "Savings"),
    ("credit_card", "Credit Card"),
    ("gift_card", "Gift Card"),
    ("wallet", "Wallet"),
    ("digital_wallet", "Digital Wallet"),
    ("loan", "Loan"),
    ("other", "Other"),
]

ACCOUNT_TYPE_VALUES: list[str] = [value for value, _ in ACCOUNT_TYPE_OPTIONS]

# ---------------------------------------------------------------------------
# Liquidity tiers (unified holdings taxonomy)
# ---------------------------------------------------------------------------
#
# Every holding (account or asset/debt) falls into exactly one liquidity tier,
# determined *solely* by its type — there is no per-holding override. The three
# tiers are nested for cumulative totals:
#
#     liquid  ⊂  investable  ⊂  net_worth
#
#   - liquid     = spendable now: cash accounts minus credit-card balances.
#   - investable = liquid + semi-liquid (brokerage, crypto, HSA, "other").
#   - net_worth  = investable + illiquid (retirement, property, loans).
#
# Contributions are signed (assets add, liabilities subtract), so each tier
# total is a signed prefix sum over the tier ordering.
LiquidityTier = Literal["liquid", "semi_liquid", "illiquid"]

# Ordered from most to least liquid; the cumulative totals walk this order.
LIQUIDITY_TIERS: list[LiquidityTier] = ["liquid", "semi_liquid", "illiquid"]

# Account type -> liquidity tier. Credit cards sit in the liquid tier because
# their (negative) balance nets against spendable cash; loans sit in illiquid.
ACCOUNT_TYPE_TIER: dict[str, LiquidityTier] = {
    "checking": "liquid",
    "savings": "liquid",
    "gift_card": "liquid",
    "wallet": "liquid",
    "digital_wallet": "liquid",
    "credit_card": "liquid",
    "other": "semi_liquid",
    "loan": "illiquid",
}

# Asset-entry types (the asset_entries.type column). Subtypes the old bare
# asset/debt `kind` so assets can be classified into liquidity tiers. `loan`
# is the sole liability type (kind == "debt"); the rest are asset kinds.
AssetType = Literal[
    "brokerage",
    "crypto",
    "hsa",
    "retirement",
    "real_estate",
    "vehicle",
    "other_asset",
    "loan",
]

# Canonical, ordered (value, display label) list of asset-entry types — the
# single source of truth for asset-type selects/validation across CLI and web.
ASSET_TYPE_OPTIONS: list[tuple[str, str]] = [
    ("brokerage", "Brokerage"),
    ("crypto", "Crypto"),
    ("hsa", "HSA"),
    ("retirement", "Retirement"),
    ("real_estate", "Real Estate"),
    ("vehicle", "Vehicle"),
    ("other_asset", "Other Asset"),
    ("loan", "Loan"),
]

ASSET_TYPE_VALUES: list[str] = [value for value, _ in ASSET_TYPE_OPTIONS]

# Asset type -> liquidity tier.
ASSET_TYPE_TIER: dict[str, LiquidityTier] = {
    "brokerage": "semi_liquid",
    "crypto": "semi_liquid",
    "hsa": "semi_liquid",
    "retirement": "illiquid",
    "real_estate": "illiquid",
    "vehicle": "illiquid",
    "other_asset": "illiquid",
    "loan": "illiquid",
}

# Default tier for a holding whose type is unknown/unset: illiquid, so it still
# counts toward net worth but never inflates the spendable/investable totals.
DEFAULT_TIER: LiquidityTier = "illiquid"

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
    type: AssetType  # Liquidity-tier subtype (brokerage, retirement, loan, …)
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
    last4: str  # last 4 digits of the OFX account number ("" if unavailable)
