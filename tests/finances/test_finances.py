"""Tests for finances CLI and calculations."""

from datetime import date
from pathlib import Path
from unittest.mock import patch

from finances import (
    apply_budget_filters,
    filter_accounts_by_type,
    filter_assets_by_kind,
    fmt_day_ordinal,
    fmt_money,
    fmt_month_short,
    fmt_qty,
    fmt_recurrence_display,
    fmt_type_display,
    liquid_minus_cc,
    liquid_total,
    credit_card_total,
    projected_change_to_eom,
    net_nonliquid_paired,
    net_nonliquid_total,
)


def test_liquid_total():
    """Liquid total sums accounts that map to liquid (checking, savings, etc.)."""
    accounts = [
        {"name": "Checking", "type": "checking", "balance": 1000},
        {"name": "Amex", "type": "credit_card", "limit": 5000, "available": 4800},
    ]
    assert liquid_total(accounts) == 1000


def test_credit_card_total():
    """Credit card total = sum of (available - limit) per card."""
    accounts = [
        {"name": "Amex", "type": "credit_card", "limit": 5000, "available": 4800},
    ]
    assert credit_card_total(accounts) == -200  # 4800 - 5000


def test_liquid_minus_cc():
    """Liquid minus CC = liquid total - credit card total (CC balance = available - limit)."""
    accounts = [
        {"name": "Checking", "type": "checking", "balance": 1000},
        {"name": "Amex", "type": "credit_card", "limit": 5000, "available": 4800},
    ]
    assert liquid_minus_cc(accounts) == 800  # 1000 + (4800 - 5000) = 1000 + (-200)

    # With rewards_balance (adds to CC adjustment)
    accounts_with_rewards = [
        {"name": "Checking", "type": "checking", "balance": 1000},
        {
            "name": "Amex",
            "type": "credit_card",
            "limit": 5000,
            "available": 4800,
            "rewards_balance": 10,
        },
    ]
    assert liquid_minus_cc(accounts_with_rewards) == 810  # 1000 + (-200 + 10)


def test_projected_change_to_eom():
    """Projected change = income in month minus expenses in month."""
    # Empty -> 0
    assert projected_change_to_eom([], 2025, 2) == 0

    # Monthly income 1000, monthly expense 400 -> +600
    budget = [
        {
            "kind": "income",
            "description": "Salary",
            "amount": 1000,
            "recurrence": "monthly",
            "dayOfMonth": 1,
        },
        {
            "kind": "expense",
            "description": "Rent",
            "amount": 400,
            "recurrence": "monthly",
            "dayOfMonth": 1,
        },
    ]
    assert projected_change_to_eom(budget, 2025, 2) == 600

    # One-time income in Feb 2025
    budget_one = [
        {
            "kind": "income",
            "description": "Refund",
            "amount": 200,
            "recurrence": "one_time",
            "date": "2025-02-15",
        }
    ]
    assert projected_change_to_eom(budget_one, 2025, 2) == 200
    assert projected_change_to_eom(budget_one, 2025, 3) == 0

    # Annual expense in April
    budget_annual = [
        {
            "kind": "expense",
            "description": "Rego",
            "amount": 100,
            "recurrence": "annual",
            "month": 4,
            "dayOfYear": 1,
        }
    ]
    assert projected_change_to_eom(budget_annual, 2025, 4) == -100
    assert projected_change_to_eom(budget_annual, 2025, 2) == 0

    # Quarterly: month in quarter
    budget_quarterly = [
        {
            "kind": "income",
            "description": "Q",
            "amount": 300,
            "recurrence": "quarterly",
            "month": 2,
        }
    ]
    assert projected_change_to_eom(budget_quarterly, 2025, 2) == 300
    assert projected_change_to_eom(budget_quarterly, 2025, 5) == 300
    assert projected_change_to_eom(budget_quarterly, 2025, 1) == 0

    # Semiannual
    budget_semi = [
        {
            "kind": "income",
            "description": "S",
            "amount": 500,
            "recurrence": "semiannual",
            "month": 3,
        }
    ]
    assert projected_change_to_eom(budget_semi, 2025, 3) == 500
    assert projected_change_to_eom(budget_semi, 2025, 9) == 500
    assert projected_change_to_eom(budget_semi, 2025, 1) == 0

    # Biweekly: approx 2x per month
    budget_biweekly = [
        {
            "kind": "income",
            "description": "Pay",
            "amount": 100,
            "recurrence": "biweekly",
        }
    ]
    assert projected_change_to_eom(budget_biweekly, 2025, 2) == 200

    # Continuous monthly: prorated by day (day=0 -> full month; day=10 of April (30 days) -> 20/30 remaining)
    budget_cont = [
        {
            "kind": "income",
            "description": "Salary",
            "amount": 900,
            "recurrence": "monthly",
            "continuous": True,
        }
    ]
    assert projected_change_to_eom(budget_cont, 2025, 2, day=0) == 900
    # April 2025 has 30 days; day=10 -> 20 days remaining -> 900 * 20/30 = 600
    assert projected_change_to_eom(budget_cont, 2025, 4, day=10) == 600


def test_net_nonliquid_paired():
    """Paired net = sum(asset value - loan balance) for each assetRef (asset id) match."""
    entries = [
        {"kind": "asset", "id": 1, "name": "Home", "value": 300000},
        {"kind": "asset", "id": 2, "name": "Car", "value": 20000},
        {"kind": "debt", "name": "Mortgage", "assetRef": 1, "balance": 250000},
        {"kind": "debt", "name": "Car loan", "assetRef": 2, "balance": 10000},
    ]
    assert net_nonliquid_paired(entries) == 50000 + 10000  # 60_000

    # Debt with no assetRef is skipped
    entries_no_ref = [
        {"kind": "asset", "id": 1, "name": "Home", "value": 300000},
        {"kind": "debt", "name": "Mortgage", "balance": 250000},
    ]
    assert net_nonliquid_paired(entries_no_ref) == 0

    # Debt with assetRef that has no matching asset id is skipped
    entries_unmatched = [
        {"kind": "debt", "name": "Other", "assetRef": 99, "balance": 5000},
    ]
    assert net_nonliquid_paired(entries_unmatched) == 0


def test_net_nonliquid_total():
    """Total non-liquid net = sum(assets) - sum(loans)."""
    entries = [
        {"kind": "asset", "id": 1, "name": "Home", "value": 300000},
        {"kind": "asset", "id": 2, "name": "Car", "value": 20000},
        {"kind": "debt", "name": "Mortgage", "balance": 250000},
        {"kind": "debt", "name": "Car loan", "balance": 10000},
    ]
    assert net_nonliquid_total(entries) == 320000 - 260000  # 60_000

    # Asset with quantity (covers _asset_subtotal)
    entries_qty = [{"kind": "asset", "id": 1, "name": "X", "value": 10, "quantity": 3}]
    assert net_nonliquid_total(entries_qty) == 30


def test_fmt_money():
    """Format money: zero positive negative with accounting style."""
    assert fmt_money(0).strip() == "$0.00"
    assert "$1,234.56" in fmt_money(1234.56)
    assert "($1,234.56)" == fmt_money(-1234.56)


def test_fmt_qty():
    """Format quantity: None, 0, int with commas, float with decimals, fractions as %."""
    assert fmt_qty(None) == "-"
    assert fmt_qty(0) == "0"
    assert fmt_qty(1) == "1"
    assert fmt_qty(1000000) == "1,000,000"
    assert fmt_qty(0.40151208) == "40.15%"
    assert fmt_qty(0.5) == "50%"
    assert fmt_qty(0.001) == "0.1%"
    assert fmt_qty(1.5) == "1.5"


def test_filter_accounts_by_type():
    """Filter accounts by include/exclude type; None or empty means no filter; case insensitive."""
    accounts = [
        {"name": "A", "type": "checking", "balance": 100},
        {"name": "B", "type": "savings", "balance": 200},
        {"name": "C", "type": "credit_card", "limit": 1000, "available": 800},
        {"name": "D", "type": "loan", "balance": 5000},
    ]
    # No filter: all returned
    assert len(filter_accounts_by_type(accounts, None, None)) == 4
    assert len(filter_accounts_by_type(accounts, [], [])) == 4
    # Include only checking and savings
    included = filter_accounts_by_type(accounts, ["checking", "savings"], None)
    assert len(included) == 2
    assert {a["type"] for a in included} == {"checking", "savings"}
    # Include is case insensitive
    included_upper = filter_accounts_by_type(accounts, ["Checking", "SAVINGS"], None)
    assert len(included_upper) == 2
    # Exclude credit_card and loan
    excluded = filter_accounts_by_type(accounts, None, ["credit_card", "loan"])
    assert len(excluded) == 2
    assert {a["type"] for a in excluded} == {"checking", "savings"}
    # Both include and exclude: include first, then exclude
    both = filter_accounts_by_type(accounts, ["checking", "savings", "loan"], ["loan"])
    assert len(both) == 2
    assert {a["type"] for a in both} == {"checking", "savings"}
    # Account with missing type: (type or "").lower() is "" so not in include set
    accounts_no_type = [{"name": "X", "balance": 1}]  # no type key
    assert filter_accounts_by_type(accounts_no_type, ["checking"], None) == []


def test_apply_budget_filters():
    """Apply kind, type, and recurrence filters to unified budget list."""
    budget = [
        {
            "kind": "income",
            "description": "Salary",
            "type": "salary",
            "recurrence": "monthly",
        },
        {
            "kind": "income",
            "description": "Bonus",
            "type": "bonus",
            "recurrence": "one_time",
        },
        {
            "kind": "expense",
            "description": "Rent",
            "type": "housing",
            "recurrence": "monthly",
        },
        {
            "kind": "expense",
            "description": "Food",
            "type": "food",
            "recurrence": "monthly",
        },
    ]
    # No filter: all returned
    result = apply_budget_filters(budget)
    assert len(result) == 4

    # Include kind income only
    result = apply_budget_filters(budget, include_kinds=["income"])
    assert len(result) == 2
    assert all(e["kind"] == "income" for e in result)

    # Include kind expense only
    result = apply_budget_filters(budget, include_kinds=["expense"])
    assert len(result) == 2
    assert all(e["kind"] == "expense" for e in result)

    # Include types: only matching
    result = apply_budget_filters(budget, include_types=["salary", "housing"])
    assert len(result) == 2
    assert {e["type"] for e in result} == {"salary", "housing"}

    # Exclude types
    result = apply_budget_filters(budget, exclude_types=["bonus", "food"])
    assert len(result) == 2
    assert {e["type"] for e in result} == {"salary", "housing"}

    # Include recurrence
    result = apply_budget_filters(budget, include_recurrence=["monthly"])
    assert len(result) == 3  # Salary + Rent + Food
    assert all(e["recurrence"] == "monthly" for e in result)

    # Exclude recurrence
    result = apply_budget_filters(budget, exclude_recurrence=["one_time"])
    assert len(result) == 3
    assert all(e["recurrence"] != "one_time" for e in result)


def test_filter_assets_by_kind():
    """Filter unified assets list by kind. Empty/None means no filter."""
    entries = [
        {"kind": "asset", "id": 1, "name": "Home", "value": 300000},
        {"kind": "debt", "name": "Mortgage", "balance": 250000},
    ]
    # No filter: all returned (copies)
    result = filter_assets_by_kind(entries, None)
    assert len(result) == 2
    result = filter_assets_by_kind(entries, [])
    assert len(result) == 2
    # Asset only
    result = filter_assets_by_kind(entries, ["asset"])
    assert len(result) == 1
    assert result[0]["kind"] == "asset"
    # Debt only
    result = filter_assets_by_kind(entries, ["debt"])
    assert len(result) == 1
    assert result[0]["kind"] == "debt"
    # Both kinds: all returned
    result = filter_assets_by_kind(entries, ["asset", "debt"])
    assert len(result) == 2
    # Non-matching kind: empty
    result = filter_assets_by_kind(entries, ["other"])
    assert len(result) == 0


def test_fmt_type_display():
    """Format type for display: None/'-' -> '-'; snake_case -> Title Case."""
    assert fmt_type_display(None) == "-"
    assert fmt_type_display("") == "-"
    assert fmt_type_display("-") == "-"
    assert fmt_type_display("credit_card") == "Credit Card"
    assert fmt_type_display("salary") == "Salary"
    assert fmt_type_display("one_time") == "One Time"


def test_fmt_recurrence_display():
    """Format recurrence for display: None/'-' -> '-'; snake_case -> Title Case."""
    assert fmt_recurrence_display(None) == "-"
    assert fmt_recurrence_display("-") == "-"
    assert fmt_recurrence_display("one_time") == "One Time"
    assert fmt_recurrence_display("monthly") == "Monthly"
    assert fmt_recurrence_display("biweekly") == "Biweekly"


def test_fmt_day_ordinal():
    """Format day of month: 1st, 2nd, 3rd, 4th, 11th/12th/13th, 21st."""
    assert fmt_day_ordinal(1) == "1st"
    assert fmt_day_ordinal(2) == "2nd"
    assert fmt_day_ordinal(3) == "3rd"
    assert fmt_day_ordinal(4) == "4th"
    assert fmt_day_ordinal(5) == "5th"
    assert fmt_day_ordinal(11) == "11th"
    assert fmt_day_ordinal(12) == "12th"
    assert fmt_day_ordinal(13) == "13th"
    assert fmt_day_ordinal(21) == "21st"
    assert fmt_day_ordinal(22) == "22nd"
    assert fmt_day_ordinal(23) == "23rd"


def test_fmt_month_short():
    """Format month (1-12) as short name."""
    assert fmt_month_short(1) == "Jan"
    assert fmt_month_short(2) == "Feb"
    assert fmt_month_short(12) == "Dec"


def test_projected_change_to_eom_day_none_uses_today():
    """When day=None and (year, month) is current month, uses today's day for remainder."""
    budget = [
        {
            "kind": "income",
            "description": "Salary",
            "amount": 900,
            "recurrence": "monthly",
            "continuous": True,
        }
    ]
    # Patch date.today() to 2025-02-14 so day=14 is used for Feb 2025
    with patch("finances.calculations.date") as mock_date:
        mock_date.today.return_value = date(2025, 2, 14)
        # Feb 2025 has 28 days; from day 14, 14 days remaining -> 900 * 14/28 = 450
        result = projected_change_to_eom(budget, 2025, 2)
    assert result == 450


def test_projected_change_to_eom_day_none_other_month_uses_zero():
    """When day=None and (year, month) is not current month, uses day=0 (full month)."""
    budget = [
        {
            "kind": "income",
            "description": "Salary",
            "amount": 600,
            "recurrence": "monthly",
            "dayOfMonth": 1,
        }
    ]
    # Patch date.today() to 2025-05-10; ask for March 2025 -> not current month -> day=0
    with patch("finances.calculations.date") as mock_date:
        mock_date.today.return_value = date(2025, 5, 10)
        result = projected_change_to_eom(budget, 2025, 3)
    # Full month in March
    assert result == 600


def test_status_command_exits_zero(tmp_path):
    """Status command runs and exits 0 (smoke test)."""
    import sys

    from sqlalchemy import create_engine

    from finances import main
    from finances.db import init_db
    from finances.yaml_import import import_yaml

    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    init_db(engine)
    fixture = Path(__file__).parent / "fixtures" / "test_finances.yaml"
    with engine.connect() as conn:
        import_yaml(conn, fixture, name="test_finances")

    orig_argv = sys.argv
    try:
        sys.argv = ["finances.py", "--db", str(db_path), "test_finances", "status"]
        assert main() == 0
    finally:
        sys.argv = orig_argv
