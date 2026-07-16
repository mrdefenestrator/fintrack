"""Tests for finances/calculations.py — uncovered helpers and edge cases."""

from datetime import date

from finances.calculations import (
    _amount_annual,
    _budget_entry_in_month,
    _credit_card_balance_owed,
    _entry_subtotal,
    _quarter_months,
    _semiannual_other_month,
    _subtotal_remainder_of_month,
    account_funding_needed,
    net_nonliquid_paired,
    net_nonliquid_total,
)


# ---------------------------------------------------------------------------
# _credit_card_balance_owed
# ---------------------------------------------------------------------------


def test_credit_card_balance_owed():
    assert _credit_card_balance_owed({"limit": 5000, "available": 4000}) == -1000
    assert _credit_card_balance_owed({"limit": 5000, "available": 5000}) == 0
    # Missing fields returns 0
    assert _credit_card_balance_owed({}) == 0
    assert _credit_card_balance_owed({"limit": 5000}) == 0


# ---------------------------------------------------------------------------
# _entry_subtotal
# ---------------------------------------------------------------------------


def test_entry_subtotal_asset_no_qty():
    assert _entry_subtotal({"kind": "asset", "value": 100}) == 100


def test_entry_subtotal_asset_with_qty():
    assert _entry_subtotal({"kind": "asset", "value": 100, "quantity": 3}) == 300


def test_entry_subtotal_debt_no_qty():
    assert _entry_subtotal({"kind": "debt", "balance": 200}) == 200


def test_entry_subtotal_debt_with_qty():
    assert _entry_subtotal({"kind": "debt", "balance": 200, "quantity": 0.5}) == 100.0


# ---------------------------------------------------------------------------
# net_nonliquid_total / net_nonliquid_paired with debt quantity
# ---------------------------------------------------------------------------


def test_net_nonliquid_total_with_debt_quantity():
    assets = [
        {"kind": "asset", "id": 1, "name": "Home", "value": 400000},
        {"kind": "debt", "name": "Mortgage", "balance": 400000, "quantity": 0.5},
    ]
    # total = 400000 - (400000 * 0.5) = 400000 - 200000 = 200000
    assert net_nonliquid_total(assets) == 200000.0


def test_net_nonliquid_paired_with_debt_quantity():
    assets = [
        {"kind": "asset", "id": 1, "name": "Home", "value": 400000},
        {
            "kind": "debt",
            "name": "Mortgage",
            "balance": 400000,
            "quantity": 0.5,
            "assetRef": 1,
        },
    ]
    # paired = asset_subtotal - debt_subtotal = 400000 - 200000 = 200000
    assert net_nonliquid_paired(assets) == 200000.0


# ---------------------------------------------------------------------------
# _quarter_months / _semiannual_other_month
# ---------------------------------------------------------------------------


def test_quarter_months():
    assert _quarter_months(1) == {1, 4, 7, 10}
    assert _quarter_months(2) == {2, 5, 8, 11}
    assert _quarter_months(3) == {3, 6, 9, 12}


def test_semiannual_other_month():
    assert _semiannual_other_month(1) == 7
    assert _semiannual_other_month(3) == 9
    assert _semiannual_other_month(7) == 1
    assert _semiannual_other_month(12) == 6


# ---------------------------------------------------------------------------
# _amount_annual
# ---------------------------------------------------------------------------


def test_amount_annual_monthly():
    assert _amount_annual({"amount": 100, "recurrence": "monthly"}) == 1200


def test_amount_annual_biweekly():
    assert _amount_annual({"amount": 100, "recurrence": "biweekly"}) == 2600


def test_amount_annual_annual():
    assert _amount_annual({"amount": 500, "recurrence": "annual"}) == 500


def test_amount_annual_quarterly():
    assert _amount_annual({"amount": 300, "recurrence": "quarterly"}) == 1200


def test_amount_annual_semiannual():
    assert _amount_annual({"amount": 600, "recurrence": "semiannual"}) == 1200


def test_amount_annual_one_time():
    assert _amount_annual({"amount": 200, "recurrence": "one_time"}) == 200


def test_amount_annual_unknown():
    assert _amount_annual({"amount": 100, "recurrence": "daily"}) == 0.0


# ---------------------------------------------------------------------------
# _budget_entry_in_month — edge cases not covered by projected_change_to_eom
# ---------------------------------------------------------------------------


def test_budget_entry_monthly_continuous_with_day():
    # April has 30 days; day=10 -> 20 remaining -> 900 * 20/30 = 600
    entry = {"amount": 900, "recurrence": "monthly", "continuous": True}
    assert _budget_entry_in_month(entry, 2025, 4, day=10) == 600.0


def test_budget_entry_monthly_non_continuous():
    entry = {"amount": 500, "recurrence": "monthly"}
    assert _budget_entry_in_month(entry, 2025, 4) == 500


def test_budget_entry_one_time_bad_date():
    entry = {"amount": 100, "recurrence": "one_time", "date": "not-a-date"}
    assert _budget_entry_in_month(entry, 2025, 2) == 0


def test_budget_entry_one_time_no_date():
    entry = {"amount": 100, "recurrence": "one_time"}
    assert _budget_entry_in_month(entry, 2025, 2) == 0


def test_budget_entry_unknown_recurrence():
    entry = {"amount": 100, "recurrence": "daily"}
    assert _budget_entry_in_month(entry, 2025, 2) == 0


# ---------------------------------------------------------------------------
# _subtotal_remainder_of_month — edge cases
# ---------------------------------------------------------------------------


def test_subtotal_monthly_no_day_of_month():
    """Monthly without dayOfMonth treated as full month."""
    entry = {"amount": 1000, "recurrence": "monthly"}
    assert _subtotal_remainder_of_month(entry, 2025, 2, day=15) == 1000


def test_subtotal_monthly_past_day():
    """Monthly with dayOfMonth already past returns 0."""
    entry = {"amount": 1000, "recurrence": "monthly", "dayOfMonth": 5}
    assert _subtotal_remainder_of_month(entry, 2025, 2, day=10) == 0


def test_subtotal_monthly_before_day():
    """Monthly with dayOfMonth not yet reached returns full amount."""
    entry = {"amount": 1000, "recurrence": "monthly", "dayOfMonth": 20}
    assert _subtotal_remainder_of_month(entry, 2025, 2, day=10) == 1000


def test_subtotal_one_time_future_in_month():
    entry = {"amount": 200, "recurrence": "one_time", "date": "2025-02-20"}
    assert _subtotal_remainder_of_month(entry, 2025, 2, day=10) == 200


def test_subtotal_one_time_past_in_month():
    entry = {"amount": 200, "recurrence": "one_time", "date": "2025-02-05"}
    assert _subtotal_remainder_of_month(entry, 2025, 2, day=10) == 0


def test_subtotal_annual_wrong_month():
    entry = {"amount": 500, "recurrence": "annual", "month": 6, "dayOfMonth": 15}
    assert _subtotal_remainder_of_month(entry, 2025, 2, day=1) == 0


def test_subtotal_annual_right_month_before_day():
    entry = {"amount": 500, "recurrence": "annual", "month": 2, "dayOfMonth": 20}
    assert _subtotal_remainder_of_month(entry, 2025, 2, day=10) == 500


def test_subtotal_annual_right_month_past_day():
    entry = {"amount": 500, "recurrence": "annual", "month": 2, "dayOfMonth": 5}
    assert _subtotal_remainder_of_month(entry, 2025, 2, day=10) == 0


def test_subtotal_annual_no_day():
    entry = {"amount": 500, "recurrence": "annual", "month": 2}
    assert _subtotal_remainder_of_month(entry, 2025, 2, day=10) == 500


def test_subtotal_annual_both_day_fields_prefers_dayOfMonth():
    """When both dayOfMonth and dayOfYear are present, dayOfMonth wins."""
    entry = {
        "amount": 500,
        "recurrence": "annual",
        "month": 3,
        "dayOfMonth": 20,
        "dayOfYear": 5,
    }
    assert _subtotal_remainder_of_month(entry, 2025, 3, day=10) == 500
    assert _subtotal_remainder_of_month(entry, 2025, 3, day=25) == 0


def test_subtotal_quarterly_not_in_quarter():
    entry = {"amount": 300, "recurrence": "quarterly", "month": 1, "dayOfMonth": 15}
    # Month 2 is not in quarter starting month 1 (1,4,7,10)
    assert _subtotal_remainder_of_month(entry, 2025, 2, day=1) == 0


def test_subtotal_quarterly_in_quarter_before_day():
    entry = {"amount": 300, "recurrence": "quarterly", "month": 1, "dayOfMonth": 20}
    assert _subtotal_remainder_of_month(entry, 2025, 1, day=10) == 300


def test_subtotal_quarterly_in_quarter_past_day():
    entry = {"amount": 300, "recurrence": "quarterly", "month": 1, "dayOfMonth": 5}
    assert _subtotal_remainder_of_month(entry, 2025, 1, day=10) == 0


def test_subtotal_quarterly_no_day():
    entry = {"amount": 300, "recurrence": "quarterly", "month": 1}
    assert _subtotal_remainder_of_month(entry, 2025, 1, day=10) == 300


def test_subtotal_semiannual_not_in_period():
    entry = {"amount": 600, "recurrence": "semiannual", "month": 3, "dayOfMonth": 15}
    assert _subtotal_remainder_of_month(entry, 2025, 1, day=1) == 0


def test_subtotal_semiannual_in_primary_month():
    entry = {"amount": 600, "recurrence": "semiannual", "month": 3, "dayOfMonth": 20}
    assert _subtotal_remainder_of_month(entry, 2025, 3, day=10) == 600


def test_subtotal_semiannual_in_other_month():
    entry = {"amount": 600, "recurrence": "semiannual", "month": 3, "dayOfMonth": 20}
    # Other month for 3 is 9
    assert _subtotal_remainder_of_month(entry, 2025, 9, day=10) == 600


def test_subtotal_semiannual_past_day():
    entry = {"amount": 600, "recurrence": "semiannual", "month": 3, "dayOfMonth": 5}
    assert _subtotal_remainder_of_month(entry, 2025, 3, day=10) == 0


def test_subtotal_semiannual_no_day():
    entry = {"amount": 600, "recurrence": "semiannual", "month": 3}
    assert _subtotal_remainder_of_month(entry, 2025, 3, day=10) == 600


def test_subtotal_unknown_recurrence():
    entry = {"amount": 100, "recurrence": "daily"}
    assert _subtotal_remainder_of_month(entry, 2025, 2, day=10) == 0


# ---------------------------------------------------------------------------
# account_funding_needed
# ---------------------------------------------------------------------------

_TODAY = date(2026, 2, 15)

_CHECKING = {"id": 1, "name": "Main Checking", "type": "checking", "balance": 1000.0}
_CC = {
    "id": 2,
    "name": "Rewards Card",
    "type": "credit_card",
    "limit": 5000.0,
    "available": 4500.0,
    "paymentAccountRef": 1,
    "statement_balance": 400.0,
}
_CC_NO_STMT = {
    "id": 3,
    "name": "Travel Card",
    "type": "credit_card",
    "limit": 3000.0,
    "available": 2700.0,
    "paymentAccountRef": 1,
}


def test_funding_no_obligations():
    """No CCs, no direct expenses → funding_needed = max(0, reserve - balance)."""
    acc = {"id": 1, "name": "Savings", "type": "savings", "balance": 500.0}
    result = account_funding_needed(acc, [acc], [], _TODAY, default_reserve=300.0)
    assert result["cc_total"] == 0.0
    assert result["expenses_total"] == 0.0
    assert result["reserve"] == 300.0
    assert result["total_obligations"] == 300.0
    assert result["funding_needed"] == 0.0
    assert result["surplus"] == 200.0


def test_funding_no_obligations_underfunded():
    """Balance below reserve → funding needed."""
    acc = {"id": 1, "name": "Checking", "type": "checking", "balance": 100.0}
    result = account_funding_needed(acc, [acc], [], _TODAY, default_reserve=300.0)
    assert result["funding_needed"] == 200.0
    assert result["surplus"] == 0.0


def test_funding_cc_with_statement_balance():
    """CC with statement_balance set → uses statement_balance."""
    result = account_funding_needed(
        _CHECKING, [_CHECKING, _CC], [], _TODAY, default_reserve=300.0
    )
    assert result["cc_total"] == 400.0
    assert len(result["cc_items"]) == 1
    assert result["cc_items"][0][1] == 400.0


def test_funding_cc_without_statement_balance():
    """CC lacking statement_balance falls back to limit - available."""
    result = account_funding_needed(
        _CHECKING, [_CHECKING, _CC_NO_STMT], [], _TODAY, default_reserve=0.0
    )
    # limit=3000, available=2700 → owed = 300
    assert result["cc_total"] == 300.0


def test_funding_cc_not_linked_to_account():
    """CC whose paymentAccountRef points elsewhere doesn't count."""
    cc_other = {
        "id": 4,
        "name": "Other Card",
        "type": "credit_card",
        "limit": 2000.0,
        "available": 1800.0,
        "paymentAccountRef": 99,
        "statement_balance": 200.0,
    }
    result = account_funding_needed(
        _CHECKING, [_CHECKING, cc_other], [], _TODAY, default_reserve=0.0
    )
    assert result["cc_total"] == 0.0


def test_funding_monthly_expense():
    """Monthly expense linked to account is always included."""
    acc = {"id": 1, "name": "Checking", "type": "checking", "balance": 2000.0}
    expense = {
        "kind": "expense",
        "description": "Rent",
        "amount": 1500.0,
        "recurrence": "monthly",
        "autoAccountRef": 1,
    }
    result = account_funding_needed(acc, [acc], [expense], _TODAY, default_reserve=0.0)
    assert result["expenses_total"] == 1500.0
    assert len(result["expense_items"]) == 1


def test_funding_annual_expense_in_month():
    """Annual expense whose month matches today is included."""
    acc = {"id": 1, "name": "Checking", "type": "checking", "balance": 500.0}
    expense = {
        "kind": "expense",
        "description": "Tax Filing",
        "amount": 200.0,
        "recurrence": "annual",
        "month": 2,  # February — matches _TODAY
        "autoAccountRef": 1,
    }
    result = account_funding_needed(acc, [acc], [expense], _TODAY, default_reserve=0.0)
    assert result["expenses_total"] == 200.0


def test_funding_annual_expense_out_of_month():
    """Annual expense whose month doesn't match today is excluded."""
    acc = {"id": 1, "name": "Checking", "type": "checking", "balance": 500.0}
    expense = {
        "kind": "expense",
        "description": "Insurance",
        "amount": 600.0,
        "recurrence": "annual",
        "month": 6,  # June — doesn't match _TODAY (Feb)
        "autoAccountRef": 1,
    }
    result = account_funding_needed(acc, [acc], [expense], _TODAY, default_reserve=0.0)
    assert result["expenses_total"] == 0.0
    assert result["expense_items"] == []


def test_funding_one_time_expense_in_month():
    """One-time expense in the current month is included."""
    acc = {"id": 1, "name": "Checking", "type": "checking", "balance": 500.0}
    expense = {
        "kind": "expense",
        "description": "Repair",
        "amount": 350.0,
        "recurrence": "one_time",
        "date": "2026-02-20",
        "autoAccountRef": 1,
    }
    result = account_funding_needed(acc, [acc], [expense], _TODAY, default_reserve=0.0)
    assert result["expenses_total"] == 350.0


def test_funding_one_time_expense_out_of_month():
    """One-time expense outside current month is excluded."""
    acc = {"id": 1, "name": "Checking", "type": "checking", "balance": 500.0}
    expense = {
        "kind": "expense",
        "description": "Repair",
        "amount": 350.0,
        "recurrence": "one_time",
        "date": "2026-03-05",
        "autoAccountRef": 1,
    }
    result = account_funding_needed(acc, [acc], [expense], _TODAY, default_reserve=0.0)
    assert result["expenses_total"] == 0.0


def test_funding_expense_wrong_account():
    """Expense linked to a different account is not counted."""
    acc = {"id": 1, "name": "Checking", "type": "checking", "balance": 500.0}
    expense = {
        "kind": "expense",
        "description": "Groceries",
        "amount": 400.0,
        "recurrence": "monthly",
        "autoAccountRef": 2,
    }
    result = account_funding_needed(acc, [acc], [expense], _TODAY, default_reserve=0.0)
    assert result["expenses_total"] == 0.0


def test_funding_income_entry_ignored():
    """Income entries are not counted as obligations."""
    acc = {"id": 1, "name": "Checking", "type": "checking", "balance": 500.0}
    income = {
        "kind": "income",
        "description": "Salary",
        "amount": 5000.0,
        "recurrence": "monthly",
        "autoAccountRef": 1,
    }
    result = account_funding_needed(acc, [acc], [income], _TODAY, default_reserve=0.0)
    assert result["expenses_total"] == 0.0


def test_funding_surplus():
    """Balance well above obligations → surplus, funding_needed = 0."""
    acc = {"id": 1, "name": "Checking", "type": "checking", "balance": 5000.0}
    result = account_funding_needed(acc, [acc], [], _TODAY, default_reserve=300.0)
    assert result["funding_needed"] == 0.0
    assert result["surplus"] == 4700.0


def test_funding_minimum_balance_as_reserve():
    """account.minimum_balance is used as reserve instead of default_reserve."""
    acc = {
        "id": 1,
        "name": "Checking",
        "type": "checking",
        "balance": 1000.0,
        "minimum_balance": 500.0,
    }
    result = account_funding_needed(acc, [acc], [], _TODAY, default_reserve=300.0)
    assert result["reserve"] == 500.0
    assert result["surplus"] == 500.0


def test_funding_combined():
    """CC + direct expenses + reserve totals correctly."""
    acc = {"id": 1, "name": "Checking", "type": "checking", "balance": 1000.0}
    cc = {
        "id": 2,
        "type": "credit_card",
        "limit": 5000.0,
        "available": 4600.0,
        "paymentAccountRef": 1,
        "statement_balance": 350.0,
    }
    expense = {
        "kind": "expense",
        "description": "Utilities",
        "amount": 120.0,
        "recurrence": "monthly",
        "autoAccountRef": 1,
    }
    result = account_funding_needed(
        acc, [acc, cc], [expense], _TODAY, default_reserve=200.0
    )
    assert result["cc_total"] == 350.0
    assert result["expenses_total"] == 120.0
    assert result["reserve"] == 200.0
    assert result["total_obligations"] == 670.0
    assert result["funding_needed"] == 0.0
    assert result["surplus"] == 330.0
