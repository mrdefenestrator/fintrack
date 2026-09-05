"""Unscheduled-spend estimator tests."""

from datetime import date
from decimal import Decimal

from fintrack.accounts.repository import add_account
from fintrack.budget.repository import add_budget_entry, get_budget_entries
from fintrack.projections.engine import project
from fintrack.projections.estimators import (
    unscheduled_monthly_total,
    unscheduled_spend_by_category,
)
from tests.projections.conftest import seed_transactions

TODAY = date(2026, 7, 16)


def _seed_three_months(conn, account_id, merchant, category, monthly_amount):
    seed_transactions(
        conn,
        account_id,
        [
            (date(2026, 4, 10), monthly_amount, merchant, category),
            (date(2026, 5, 10), monthly_amount, merchant, category),
            (date(2026, 6, 10), monthly_amount, merchant, category),
        ],
    )


def test_trailing_average_of_net_spend_categories(conn, snapshot_id):
    account_id = add_account(conn, snapshot_id, {"name": "Chk", "type": "checking"})
    _seed_three_months(conn, account_id, "WHOLE FOODS", "Groceries", "-300.00")
    _seed_three_months(conn, account_id, "PAYROLL", "Income", "5000.00")

    result = unscheduled_spend_by_category(conn, snapshot_id, [], today=TODAY)
    # Net-spend categories only: income (positive average) is not "spend".
    assert result == {"Groceries": Decimal(-300)}
    assert unscheduled_monthly_total(result) == Decimal(-300)


def test_budget_claimed_categories_are_excluded(conn, snapshot_id):
    account_id = add_account(conn, snapshot_id, {"name": "Chk", "type": "checking"})
    _seed_three_months(conn, account_id, "WHOLE FOODS", "Groceries", "-300.00")
    _seed_three_months(conn, account_id, "NETFLIX", "Subscriptions", "-20.00")
    add_budget_entry(
        conn,
        snapshot_id,
        {
            "kind": "expense",
            "description": "food budget",
            "amount": 350,
            "recurrence": "monthly",
            "category": "Groceries",
        },
    )
    budget = get_budget_entries(conn, snapshot_id)

    result = unscheduled_spend_by_category(conn, snapshot_id, budget, today=TODAY)
    assert result == {"Subscriptions": Decimal(-20)}


def test_transfers_are_excluded(conn, snapshot_id):
    account_id = add_account(conn, snapshot_id, {"name": "Chk", "type": "checking"})
    _seed_three_months(conn, account_id, "CC PAYMENT", "Transfer", "-800.00")
    result = unscheduled_spend_by_category(conn, snapshot_id, [], today=TODAY)
    assert result == {}


def test_only_transactions_in_window_count(conn, snapshot_id):
    account_id = add_account(conn, snapshot_id, {"name": "Chk", "type": "checking"})
    seed_transactions(
        conn,
        account_id,
        [
            (date(2026, 3, 31), "-900.00", "OLD SHOP", "Shopping"),  # before window
            (date(2026, 7, 2), "-900.00", "NEW SHOP", "Shopping"),  # current month
            (date(2026, 5, 15), "-90.00", "MID SHOP", "Shopping"),  # in window
        ],
    )
    result = unscheduled_spend_by_category(conn, snapshot_id, [], today=TODAY)
    assert result == {"Shopping": Decimal(-30)}  # -90 over 3 months


def test_engine_applies_estimate_to_unassigned_prorated(conn, snapshot_id):
    account_id = add_account(
        conn, snapshot_id, {"name": "Chk", "type": "checking", "balance": 1000}
    )
    _seed_three_months(conn, account_id, "WHOLE FOODS", "Groceries", "-310.00")

    result = project(conn, snapshot_id, months=2, include_estimate=True, today=TODAY)
    assert result["estimate"]["monthly"] == Decimal(-310)
    assert result["estimate"]["by_category"] == {"Groceries": Decimal(-310)}
    # July has 31 days and 15 remain after the 16th: month 0 is prorated.
    month0 = Decimal(-310) * 15 / 31
    assert result["unassigned"] == [month0, month0 + Decimal(-310)]
    assert result["liquid"] == [1000 + month0, 1000 + month0 - 310]


def test_engine_without_estimate_reports_none(conn, snapshot_id):
    add_account(conn, snapshot_id, {"name": "Chk", "type": "checking"})
    result = project(conn, snapshot_id, months=2, today=TODAY)
    assert result["estimate"] is None
