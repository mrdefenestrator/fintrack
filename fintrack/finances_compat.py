"""Transitional façade replicating the old `finances` package API.

The finances web routes and tests were written against `import finances`
attribute access. This module re-exports the same names from their new
fintrack locations so those callers only change their import line. It goes
away when the finances web UI and CLI are absorbed in the unified-web and
CLI-consolidation phases.
"""

from fintrack.core.types import (
    Account,
    AccountType,
    AssetEntry,
    BudgetEntry,
    ExpenseType,
    FinancesData,
    IncomeType,
    Recurrence,
)
from fintrack.networth.calculations import (
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
from fintrack.core.filters import (
    apply_budget_filters,
    filter_accounts_by_type,
    filter_assets_by_kind,
)
from fintrack.core.formatting import (
    fmt_day_ordinal,
    fmt_money,
    fmt_month_short,
    fmt_qty,
    fmt_recurrence_display,
    fmt_type_display,
)
from fintrack.cli.finances_cli import main
from fintrack.core.tables import (
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
