"""
Finances tracker: shared logic for CLI and web.

Submodules:
  types        - TypedDict definitions for Account, BudgetEntry, AssetEntry, FinancesData
  loader       - load_finances_from_db
  calculations - liquid_total, liquid_minus_cc, projected_change_to_eom, net_nonliquid_*, constants
  filters      - filter_accounts_by_type, apply_budget_filters, filter_assets_by_kind
  formatting   - fmt_money, fmt_qty, fmt_type_display, fmt_recurrence_display, fmt_day_ordinal, fmt_month_short
  tables       - _account_display_by_id, _build_*_table (for CLI and web)
  repository/  - CRUD operations against SQLite (accounts, budget, assets, snapshots)
  cli          - main (CLI entrypoint)
"""

from .types import (
    Account,
    AccountType,
    AssetEntry,
    BudgetEntry,
    ExpenseType,
    FinancesData,
    IncomeType,
    Recurrence,
)
from .calculations import (
    ACCOUNT_TYPES,
    ASSETS_KINDS,
    BUDGET_ALL_TYPES,
    BUDGET_EXPENSE_TYPES,
    BUDGET_INCOME_TYPES,
    BUDGET_KINDS,
    RECURRENCE_OPTIONS,
    _ACCOUNT_TYPE_TO_CALCULATION,
    account_funding_needed,
    credit_card_total,
    liquid_minus_cc,
    liquid_total,
    net_nonliquid_paired,
    net_nonliquid_total,
    projected_change_to_eom,
)
from .filters import (
    apply_budget_filters,
    filter_accounts_by_type,
    filter_assets_by_kind,
)
from .formatting import (
    fmt_day_ordinal,
    fmt_money,
    fmt_month_short,
    fmt_qty,
    fmt_recurrence_display,
    fmt_type_display,
)
from .cli import main
from .tables import (
    _account_display_by_id,
    _build_accounts_table,
    _build_budget_table,
    _build_funding_table,
    _build_net_worth_table,
)

__all__ = [
    # Types
    "Account",
    "AccountType",
    "AssetEntry",
    "BudgetEntry",
    "ExpenseType",
    "FinancesData",
    "IncomeType",
    "Recurrence",
    # Constants
    "ACCOUNT_TYPES",
    "ASSETS_KINDS",
    "BUDGET_ALL_TYPES",
    "BUDGET_EXPENSE_TYPES",
    "BUDGET_INCOME_TYPES",
    "BUDGET_KINDS",
    "RECURRENCE_OPTIONS",
    # Functions
    "apply_budget_filters",
    "credit_card_total",
    "filter_accounts_by_type",
    "filter_assets_by_kind",
    "fmt_day_ordinal",
    "fmt_money",
    "fmt_month_short",
    "fmt_qty",
    "fmt_recurrence_display",
    "fmt_type_display",
    "liquid_minus_cc",
    "liquid_total",
    "main",
    "net_nonliquid_paired",
    "net_nonliquid_total",
    "projected_change_to_eom",
    "_ACCOUNT_TYPE_TO_CALCULATION",
    "_account_display_by_id",
    "_build_accounts_table",
    "_build_budget_table",
    "_build_funding_table",
    "_build_net_worth_table",
    "account_funding_needed",
]
