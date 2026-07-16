from datetime import date
from decimal import Decimal
from typing import TypedDict


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
