"""Monthly cash flow and savings rate (#99): fintrack.insights.cash_flow and
the Overview card built on it."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from fintrack.accounts.repository import add_account
from fintrack.budget.repository import add_budget_entry
from fintrack.core.models import transactions
from fintrack.insights.cash_flow import (
    budgeted_net_in_month,
    compute_cash_flow,
    get_cash_flow,
    savings_rate,
    trailing_months_start,
)
from fintrack.insights.overview import build_overview
from fintrack.ledger.repository.duplicates import (
    add_duplicate_flags,
    resolve_duplicate,
)
from tests.projections.conftest import seed_transactions

TODAY = date(2026, 11, 7)


def _row(year, month, category, total):
    return {"year": year, "month": month, "category": category, "total": total}


def test_income_spending_net_and_rate_per_month():
    rows = [
        _row(2026, 9, "Income", Decimal(5000)),
        _row(2026, 9, "Groceries", Decimal(-600)),
        _row(2026, 9, "Housing", Decimal(-1800)),
        _row(2026, 9, "Transfer", Decimal(-900)),
    ]
    flow = compute_cash_flow(rows, [(2026, 9)])
    (sep,) = flow["months"]
    assert sep["income"] == 5000
    assert sep["spending"] == 2400  # Transfer left out
    assert sep["net"] == 2600
    assert sep["rate"] == 52
    assert sep["budgeted_net"] is None  # no budget passed


def test_refunds_are_negative_spending_not_income():
    rows = [
        _row(2026, 9, "Income", Decimal(1000)),
        _row(2026, 9, "Shopping", Decimal(-300)),
        _row(2026, 9, "Shopping", Decimal(100)),  # refund
    ]
    (sep,) = compute_cash_flow(rows, [(2026, 9)])["months"]
    assert sep["income"] == 1000
    assert sep["spending"] == 200


def test_savings_rate_blank_without_income():
    assert savings_rate(-500.0, 0.0) is None
    assert savings_rate(-500.0, -10.0) is None
    assert savings_rate(None, 100.0) is None
    (m,) = compute_cash_flow([_row(2026, 9, "Dining", -50)], [(2026, 9)])["months"]
    assert m["income"] == 0
    assert m["rate"] is None


def test_average_skips_the_month_in_progress_and_is_net_over_income():
    rows = [
        _row(2026, 9, "Income", 4000),
        _row(2026, 9, "Dining", -3000),
        _row(2026, 10, "Income", 6000),
        _row(2026, 10, "Dining", -3000),
        _row(2026, 11, "Income", 100),  # in progress: shown, not averaged
    ]
    months = [(2026, 9), (2026, 10), (2026, 11)]
    flow = compute_cash_flow(rows, months, in_progress=(2026, 11))
    assert flow["in_progress"] == (2026, 11)
    assert flow["avg_month_count"] == 2
    avg = flow["average"]
    assert avg["income"] == 5000
    assert avg["spending"] == 3000
    assert avg["net"] == 2000
    # avg net / avg income (40%), not the mean of 25% and 50% (37.5%).
    assert avg["rate"] == 40
    assert flow["months"][2]["rate"] == 100


def test_average_is_blank_with_only_a_month_in_progress():
    flow = compute_cash_flow(
        [_row(2026, 11, "Income", 100)], [(2026, 11)], in_progress=(2026, 11)
    )
    assert flow["average"]["net"] is None
    assert flow["average"]["rate"] is None


def test_budgeted_net_follows_each_entrys_months():
    budget = [
        {"kind": "income", "amount": 5000, "recurrence": "monthly"},
        {"kind": "expense", "amount": 1800, "recurrence": "monthly"},
        {"kind": "expense", "amount": 1200, "recurrence": "annual", "month": 11},
        # Moving money between your own accounts isn't planned spending.
        {
            "kind": "expense",
            "amount": 500,
            "recurrence": "monthly",
            "category": "Transfer",
        },
    ]
    assert budgeted_net_in_month(budget, 2026, 10) == 3200
    assert budgeted_net_in_month(budget, 2026, 11) == 2000
    flow = compute_cash_flow([], [(2026, 10), (2026, 11)], budget=budget)
    assert [m["budgeted_net"] for m in flow["months"]] == [3200, 2000]
    assert flow["average"]["budgeted_net"] == 2600


def test_trailing_months_start():
    assert trailing_months_start(TODAY, 12) == date(2025, 12, 1)
    assert trailing_months_start(date(2026, 1, 15), 1) == date(2026, 1, 1)
    assert trailing_months_start(date(2026, 1, 15), 2) == date(2025, 12, 1)


def _checking_and_card(conn, snapshot_id):
    checking = add_account(conn, snapshot_id, {"name": "Chk", "type": "checking"})
    card = add_account(conn, snapshot_id, {"name": "Card", "type": "credit_card"})
    return checking, card


def test_card_payment_is_not_counted_twice(conn, snapshot_id):
    """Paying the card moves money out of checking and into the card; both legs
    are Transfer, so only the card purchases count as spending."""
    checking, card = _checking_and_card(conn, snapshot_id)
    seed_transactions(
        conn,
        checking,
        [
            (date(2026, 10, 1), Decimal(4000), "PAYROLL", "Income"),
            (date(2026, 10, 20), Decimal(-700), "CARD PAYMENT", "Transfer"),
        ],
    )
    seed_transactions(
        conn,
        card,
        [
            (date(2026, 10, 5), Decimal(-500), "GROCER", "Groceries"),
            (date(2026, 10, 9), Decimal(-200), "DINER", "Dining"),
            (date(2026, 10, 20), Decimal(700), "PAYMENT THANK YOU", "Transfer"),
        ],
    )
    flow = get_cash_flow(
        conn,
        snapshot_id,
        start=date(2026, 10, 1),
        end=date(2026, 10, 31),
        today=TODAY,
    )
    (oct_,) = flow["months"]
    assert (oct_["income"], oct_["spending"], oct_["net"]) == (4000, 700, 3300)
    assert flow["in_progress"] is None  # window ends before this month


def test_hidden_duplicates_are_left_out(conn, snapshot_id):
    checking, _ = _checking_and_card(conn, snapshot_id)
    seed_transactions(
        conn,
        checking,
        [
            (date(2026, 10, 1), Decimal(4000), "PAYROLL", "Income"),
            (date(2026, 10, 2), Decimal(4000), "PAYROLL", "Income"),
        ],
    )
    first, second = conn.execute(
        select(transactions.c.id).order_by(transactions.c.id)
    ).scalars()
    add_duplicate_flags(conn, [(second, first, "same amount")], source="scan")
    resolve_duplicate(conn, second, "duplicate")
    flow = get_cash_flow(
        conn,
        snapshot_id,
        start=date(2026, 10, 1),
        end=date(2026, 10, 31),
        today=TODAY,
    )
    assert flow["months"][0]["income"] == 4000


def test_overview_cash_flow_card(conn, snapshot_id):
    checking, _ = _checking_and_card(conn, snapshot_id)
    seed_transactions(
        conn,
        checking,
        [
            (date(2026, 9, 1), Decimal(5000), "PAYROLL", "Income"),
            (date(2026, 9, 3), Decimal(-4000), "LANDLORD", "Housing"),
            (date(2026, 10, 1), Decimal(5000), "PAYROLL", "Income"),
            (date(2026, 10, 3), Decimal(-2000), "LANDLORD", "Housing"),
            (date(2026, 11, 1), Decimal(5000), "PAYROLL", "Income"),
            (date(2026, 11, 4), Decimal(-1000), "GROCER", "Groceries"),
            (date(2024, 1, 1), Decimal(9999), "OLD", "Income"),  # outside window
        ],
    )
    add_budget_entry(
        conn,
        snapshot_id,
        {"kind": "income", "description": "Pay", "amount": 1, "recurrence": "monthly"},
    )
    card = build_overview(conn, snapshot_id, today=TODAY)["cash_flow"]
    assert card["month_label"] == "November"
    assert card["has_this_month"] is True
    m = card["this_month"]
    assert (m["income"], m["spending"], m["rate"]) == (5000, 1000, 80)
    assert card["avg_month_count"] == 2  # Sep and Oct; November is in progress
    assert card["average"]["net"] == 2000
    assert card["average"]["rate"] == 40


def test_overview_cash_flow_card_without_transactions(conn, snapshot_id):
    card = build_overview(conn, snapshot_id, today=TODAY)["cash_flow"]
    assert card["has_this_month"] is False
    assert card["this_month"]["rate"] is None
    assert card["average"]["net"] is None
