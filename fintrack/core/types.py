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

# ---------------------------------------------------------------------------
# Liquidity tiers + the unified holding-type taxonomy
# ---------------------------------------------------------------------------
#
# Every holding (account or asset/debt) falls into exactly one liquidity tier,
# determined by its **type** with one exception — a holding denominated in a
# non-USD symbol (unit != "USD") is capped at semi-liquid, because a symbol has
# sale friction (see calculations._tier_for). The three tiers nest for the
# cumulative totals:
#
#     liquid  ⊂  investable  ⊂  net_worth
#
#   - liquid     = spendable now: cash accounts minus credit-card balances.
#   - investable = liquid + semi-liquid (brokerage, HSA, symbol wallets).
#   - net_worth  = investable + illiquid (retirement, property, loans).
#
# Contributions are signed (assets add, liabilities subtract), so each tier
# total is a signed prefix sum over the tier ordering.
LiquidityTier = Literal["liquid", "semi_liquid", "illiquid"]

# Ordered from most to least liquid; the cumulative totals walk this order.
LIQUIDITY_TIERS: list[LiquidityTier] = ["liquid", "semi_liquid", "illiquid"]

# The single holding-type vocabulary, shared by accounts and asset_entries.
# There is deliberately no catch-all ("other"): a holding either has a
# meaningful type or is left unclassified (NULL). "crypto" is not a type — a
# crypto holding is a digital wallet denominated in a symbol unit (unit=BTC),
# so its liquidity comes from the symbol cap, not a dedicated type.
HoldingType = Literal[
    "checking",
    "savings",
    "wallet",
    "digital_wallet",
    "gift_card",
    "credit_card",
    "loan",
    "brokerage",
    "hsa",
    "retirement",
    "real_estate",
    "vehicle",
]
AccountType = HoldingType  # backwards-compatible aliases
AssetType = HoldingType

# type -> base liquidity tier (before the non-USD symbol cap). Credit cards are
# liquid because their (negative) balance nets against spendable cash.
HOLDING_TYPE_TIER: dict[str, LiquidityTier] = {
    "checking": "liquid",
    "savings": "liquid",
    "wallet": "liquid",  # physical cash and uncashed checks in hand
    "digital_wallet": "liquid",  # symbol-denominated wallets drop via the cap
    "gift_card": "liquid",
    "credit_card": "liquid",
    "loan": "illiquid",
    "brokerage": "semi_liquid",
    "hsa": "semi_liquid",
    "retirement": "illiquid",
    "real_estate": "illiquid",
    "vehicle": "illiquid",
}

# Canonical (value, label) for every type — the source of truth for display
# labels app-wide.
HOLDING_TYPE_OPTIONS: list[tuple[str, str]] = [
    ("checking", "Checking"),
    ("savings", "Savings"),
    ("wallet", "Wallet"),
    ("digital_wallet", "Digital Wallet"),
    ("gift_card", "Gift Card"),
    ("credit_card", "Credit Card"),
    ("loan", "Loan"),
    ("brokerage", "Brokerage"),
    ("hsa", "HSA"),
    ("retirement", "Retirement"),
    ("real_estate", "Real Estate"),
    ("vehicle", "Vehicle"),
]
HOLDING_TYPE_LABELS: dict[str, str] = dict(HOLDING_TYPE_OPTIONS)
HOLDING_TYPE_VALUES: list[str] = [value for value, _ in HOLDING_TYPE_OPTIONS]

# Curated per-context views onto the one vocabulary: which types the Accounts
# vs Assets selectors offer. Both draw from HOLDING_TYPE_* (one taxonomy, one
# tier map); the split is only about which choices make sense where. (loan and
# digital_wallet appear in both: a loan account or a mortgage debt; a Venmo
# wallet or a crypto wallet.)
_ACCOUNT_TYPE_KEYS = [
    "checking",
    "savings",
    "wallet",
    "digital_wallet",
    "gift_card",
    "credit_card",
    "loan",
]
_ASSET_TYPE_KEYS = [
    "brokerage",
    "hsa",
    "retirement",
    "real_estate",
    "vehicle",
    "digital_wallet",
    "loan",
]
ACCOUNT_TYPE_OPTIONS: list[tuple[str, str]] = [
    (v, HOLDING_TYPE_LABELS[v]) for v in _ACCOUNT_TYPE_KEYS
]
ACCOUNT_TYPE_VALUES: list[str] = list(_ACCOUNT_TYPE_KEYS)
ASSET_TYPE_OPTIONS: list[tuple[str, str]] = [
    (v, HOLDING_TYPE_LABELS[v]) for v in _ASSET_TYPE_KEYS
]
ASSET_TYPE_VALUES: list[str] = list(_ASSET_TYPE_KEYS)

# Default tier for a holding whose type is unknown/unset: illiquid, so it still
# counts toward net worth but never inflates the spendable/investable totals.
DEFAULT_TIER: LiquidityTier = "illiquid"

# ---------------------------------------------------------------------------
# Holding groups (the four subtype tables behind the Holdings sheet)
# ---------------------------------------------------------------------------
#
# Every holding lives in exactly one group; the group picks its detail table
# (cash_details / credit_card_details / loan_details / asset_details) and the
# Holdings sheet renders one band per group. group_key is redundant with type
# for credit cards and loans (enforced by a CHECK) — the type column keeps the
# tier map and Type filter working off one vocabulary, while group_key is the
# discriminator the subtype foreign keys hang off.
GroupKey = Literal["cash", "credit_card", "loan", "asset"]
GROUP_KEYS: tuple[str, ...] = ("cash", "credit_card", "loan", "asset")

# Which types each group admits (mirrored by ck_holdings_type_matches_group).
# The asset group alone may leave type NULL (unclassified -> DEFAULT_TIER).
CASH_TYPES: tuple[str, ...] = (
    "checking",
    "savings",
    "wallet",
    "digital_wallet",
    "gift_card",
)
ASSET_GROUP_TYPES: tuple[str, ...] = (
    "brokerage",
    "hsa",
    "retirement",
    "real_estate",
    "vehicle",
    "digital_wallet",
)

# Groups whose holdings can receive statement imports (and therefore must obey
# the importable-name uniqueness rule).
IMPORTABLE_GROUPS: tuple[str, ...] = ("cash", "credit_card", "loan")


def group_for_account_type(account_type: str | None) -> GroupKey:
    """Group for an account-style holding, from its type."""
    if account_type == "credit_card":
        return "credit_card"
    if account_type == "loan":
        return "loan"
    return "cash"


def group_for_kind(kind: str) -> GroupKey:
    """Group for an asset/debt-style holding, from its kind."""
    return "loan" if kind == "debt" else "asset"


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
    category: str  # Category from the categories table
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
    unit: str  # Denomination of quantity: "USD" (default) or a ticker/symbol
    name: str  # Required - display name
    id: int  # For asset entries - unique identifier; referenced by debt assetRef
    institution: str  # Optional provider/lender name
    # Asset-only fields
    value: Decimal  # Estimated value per unit
    source: str  # Optional valuation source
    annualReturnRate: Decimal  # Annual growth (positive) or depreciation (negative)
    monthlyContribution: Decimal  # Recurring monthly contribution
    # Shared (asset + debt) fields
    quantity: Decimal  # Optional; default 1. Assets: value × quantity. Debts: balance × quantity
    # Debt-only fields
    balance: Decimal  # Amount owed (positive; stored signed in loan_details)
    assetRef: int  # Optional link to asset entry id
    paymentAccountRef: int  # Optional cash holding the loan is paid from
    interestRate: Decimal  # Optional annual rate as decimal
    originalPrincipal: Decimal  # Optional original financed principal
    termMonths: int  # Optional original amortization term in months
    originationDate: str  # Optional ISO8601 origination date
    statement_due_day_of_month: int  # Optional recurring payment due day (1-31)
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
