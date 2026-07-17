"""Projection engine tests: grid shape, budget flows, CC autopay
conservation, warnings, totals."""

from datetime import date
from decimal import Decimal

from fintrack.accounts.repository import add_account
from fintrack.budget.repository import add_budget_entry
from fintrack.networth.repository import add_asset_entry
from fintrack.projections.engine import project

TODAY = date(2026, 7, 16)


def _add(conn, snapshot_id, **fields):
    account = {"name": "Checking", "type": "checking", "balance": 0, **fields}
    return add_account(conn, snapshot_id, account)


def _series(result, account_id):
    for row in result["rows"]:
        if row["account"]["id"] == account_id:
            return row["balances"]
    raise AssertionError(f"no row for account {account_id}")


def test_grid_shape_and_labels(conn, snapshot_id):
    _add(conn, snapshot_id, balance=100)
    result = project(conn, snapshot_id, months=14, today=TODAY)
    labels = [m["label"] for m in result["months"]]
    assert labels[0] == "Jul 2026"
    assert labels[5] == "Dec 2026"
    assert labels[6] == "Jan 2027"  # year rollover
    assert labels[-1] == "Aug 2027"
    assert all(len(row["balances"]) == 14 for row in result["rows"])
    assert len(result["liquid"]) == len(result["net_worth"]) == 14


def test_income_lands_on_auto_account(conn, snapshot_id):
    checking = _add(conn, snapshot_id)
    add_budget_entry(
        conn,
        snapshot_id,
        {
            "kind": "income",
            "description": "salary",
            "amount": 100,
            "recurrence": "monthly",
            "autoAccountRef": checking,
        },
    )
    result = project(conn, snapshot_id, months=3, today=TODAY)
    assert _series(result, checking) == [Decimal(100), Decimal(200), Decimal(300)]
    assert result["liquid"] == [Decimal(100), Decimal(200), Decimal(300)]
    assert not result["has_unassigned"]


def test_entry_without_account_hits_unassigned_bucket(conn, snapshot_id):
    checking = _add(conn, snapshot_id, balance=1000)
    add_budget_entry(
        conn,
        snapshot_id,
        {
            "kind": "expense",
            "description": "misc",
            "amount": 50,
            "recurrence": "monthly",
        },
    )
    result = project(conn, snapshot_id, months=3, today=TODAY)
    # The account itself is untouched...
    assert _series(result, checking) == [Decimal(1000)] * 3
    # ...but the bucket accumulates and totals include it.
    assert result["has_unassigned"]
    assert result["unassigned"] == [Decimal(-50), Decimal(-100), Decimal(-150)]
    assert result["liquid"] == [Decimal(950), Decimal(900), Decimal(850)]


def test_cc_autopay_is_an_internal_transfer(conn, snapshot_id):
    """Conservation: Σ account balances is unchanged by autopay."""
    checking = _add(conn, snapshot_id, balance=5000)
    cc = _add(
        conn,
        snapshot_id,
        name="Visa",
        type="credit_card",
        balance=-500,
        paymentAccountRef=checking,
        statement_due_day_of_month=25,  # after TODAY.day: pays in month 0
    )
    result = project(conn, snapshot_id, months=4, today=TODAY)
    assert _series(result, cc) == [Decimal(0)] * 4
    assert _series(result, checking) == [Decimal(4500)] * 4
    initial_total = Decimal(5000) + Decimal(-500)
    for i in range(4):
        month_total = sum(row["balances"][i] for row in result["rows"])
        assert month_total == initial_total


def test_cc_autopay_waits_for_due_day_in_current_month(conn, snapshot_id):
    checking = _add(conn, snapshot_id, balance=5000)
    cc = _add(
        conn,
        snapshot_id,
        name="Visa",
        type="credit_card",
        balance=-500,
        paymentAccountRef=checking,
        statement_due_day_of_month=10,  # already passed on the 16th
    )
    result = project(conn, snapshot_id, months=3, today=TODAY)
    assert _series(result, cc) == [Decimal(-500), Decimal(0), Decimal(0)]
    assert _series(result, checking) == [Decimal(5000), Decimal(4500), Decimal(4500)]


def test_cc_charges_then_autopay_next_month(conn, snapshot_id):
    """Budget expenses charged to a CC are paid off on the next cycle."""
    checking = _add(conn, snapshot_id, balance=5000)
    cc = _add(
        conn,
        snapshot_id,
        name="Visa",
        type="credit_card",
        balance=0,
        paymentAccountRef=checking,
        statement_due_day_of_month=25,
    )
    add_budget_entry(
        conn,
        snapshot_id,
        {
            "kind": "expense",
            "description": "groceries",
            "amount": 200,
            "recurrence": "monthly",
            "autoAccountRef": cc,
        },
    )
    result = project(conn, snapshot_id, months=3, today=TODAY)
    # Each month: autopay clears last month's charges, new charges accrue.
    assert _series(result, cc) == [Decimal(-200)] * 3
    assert _series(result, checking) == [
        Decimal(5000),
        Decimal(4800),
        Decimal(4600),
    ]
    # Net worth reflects the cumulative spend regardless of which card holds it.
    assert result["net_worth"] == [Decimal(4800), Decimal(4600), Decimal(4400)]


def test_minimum_balance_warning(conn, snapshot_id):
    checking = _add(conn, snapshot_id, balance=400, minimum_balance=300)
    add_budget_entry(
        conn,
        snapshot_id,
        {
            "kind": "expense",
            "description": "rent",
            "amount": 200,
            "recurrence": "monthly",
            "autoAccountRef": checking,
        },
    )
    result = project(conn, snapshot_id, months=2, today=TODAY)
    assert _series(result, checking) == [Decimal(200), Decimal(0)]
    row = result["rows"][0]
    assert row["below"] == [True, True]
    assert len(result["warnings"]) == 2
    first = result["warnings"][0]
    assert first["month_label"] == "Jul 2026"
    assert first["balance"] == Decimal(200)
    assert first["minimum"] == Decimal(300)


def test_no_warning_without_minimum_balance(conn, snapshot_id):
    _add(conn, snapshot_id, balance=-100)
    result = project(conn, snapshot_id, months=2, today=TODAY)
    assert result["warnings"] == []


def test_net_worth_includes_static_assets(conn, snapshot_id):
    _add(conn, snapshot_id, balance=1000)
    add_asset_entry(conn, snapshot_id, {"kind": "asset", "name": "Car", "value": 9000})
    result = project(conn, snapshot_id, months=2, today=TODAY)
    assert result["liquid"] == [Decimal(1000), Decimal(1000)]
    assert result["net_worth"] == [Decimal(10000), Decimal(10000)]


def test_month_zero_prorates_remainder_of_month(conn, snapshot_id):
    checking = _add(conn, snapshot_id, balance=1000)
    # Due on the 10th: July's occurrence already happened, so month 0 is
    # untouched and later months subtract in full.
    add_budget_entry(
        conn,
        snapshot_id,
        {
            "kind": "expense",
            "description": "rent",
            "amount": 100,
            "recurrence": "monthly",
            "dayOfMonth": 10,
            "autoAccountRef": checking,
        },
    )
    result = project(conn, snapshot_id, months=3, today=TODAY)
    assert _series(result, checking) == [Decimal(1000), Decimal(900), Decimal(800)]


def test_months_clamped(conn, snapshot_id):
    _add(conn, snapshot_id)
    assert len(project(conn, snapshot_id, months=0, today=TODAY)["months"]) == 1
    assert len(project(conn, snapshot_id, months=999, today=TODAY)["months"]) == 60
