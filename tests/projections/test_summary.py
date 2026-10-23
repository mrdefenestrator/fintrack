"""Projection starting values, per-month flows, and the summary helpers."""

from datetime import date
from decimal import Decimal

from fintrack.accounts.repository import add_account
from fintrack.budget.repository import add_budget_entry
from fintrack.networth.repository import add_asset_entry
from fintrack.projections.engine import changes, month_breakdown, project, summarize

TODAY = date(2026, 7, 16)


def _account(conn, snapshot_id, **fields):
    return add_account(
        conn,
        snapshot_id,
        {"name": "Checking", "type": "checking", "balance": 0, **fields},
    )


def _budget(conn, snapshot_id, kind, description, amount, **fields):
    add_budget_entry(
        conn,
        snapshot_id,
        {
            "kind": kind,
            "description": description,
            "amount": amount,
            "recurrence": "monthly",
            **fields,
        },
    )


def _household(conn, snapshot_id):
    """Checking + autopaid card + growing asset + amortizing loan + budget flows
    (one unassigned) — touches every kind of flow the engine records."""
    checking = _account(conn, snapshot_id, balance=5000, minimum_balance=8500)
    _account(
        conn,
        snapshot_id,
        name="Visa",
        type="credit_card",
        balance=-500,
        paymentAccountRef=checking,
        statement_due_dayOfMonth=25,
    )
    _budget(
        conn,
        snapshot_id,
        "income",
        "Salary",
        3000,
        autoAccountRef=checking,
        dayOfMonth=28,
    )
    _budget(
        conn,
        snapshot_id,
        "expense",
        "Rent",
        2500,
        autoAccountRef=checking,
        dayOfMonth=1,
    )
    _budget(conn, snapshot_id, "expense", "Groceries", 600, dayOfMonth=20)
    add_asset_entry(
        conn,
        snapshot_id,
        {
            "kind": "asset",
            "name": "401k",
            "value": 10000,
            "annualReturnRate": Decimal("0.12"),
            "monthlyContribution": Decimal(100),
        },
    )
    add_asset_entry(
        conn,
        snapshot_id,
        {
            "kind": "debt",
            "name": "Car loan",
            "type": "loan",
            "balance": Decimal(1000),
            "originalPrincipal": Decimal(12000),
            "interestRate": Decimal("0.06"),
            "termMonths": 36,
            "statement_due_day_of_month": 5,
        },
    )
    return checking


def test_starting_values(conn, snapshot_id):
    _household(conn, snapshot_id)
    result = project(conn, snapshot_id, months=3, today=TODAY)
    assert result["start_liquid"] == Decimal(5000)
    # 5000 cash - 500 card + 10000 asset - 1000 loan
    assert result["start_net_worth"] == Decimal(13500)
    starts = {row["account"]["name"]: row["start"] for row in result["rows"]}
    assert starts == {
        "Checking": Decimal(5000),
        "Visa": Decimal(-500),
        "401k": Decimal(10000),
        "Car loan": Decimal(-1000),
    }


def test_flows_add_up_to_each_months_change(conn, snapshot_id):
    _household(conn, snapshot_id)
    result = project(conn, snapshot_id, months=6, today=TODAY, include_estimate=True)
    kinds = {f["kind"] for month in result["flows"] for f in month}
    assert {"autopay", "income", "expense", "growth", "contribution"} <= kinds
    assert {"interest", "payment"} <= kinds

    series = [
        (row["account"]["id"], row["start"], row["balances"]) for row in result["rows"]
    ]
    series.append((None, Decimal(0), result["unassigned"]))
    for holding_id, start, balances in series:
        for i, delta in enumerate(changes(start, balances)):
            flowed = sum(
                (
                    f["amount"]
                    for f in result["flows"][i]
                    if f["holding_id"] == holding_id
                ),
                Decimal(0),
            )
            assert flowed == delta, (holding_id, i)


def test_changes():
    assert changes(Decimal(10), [Decimal(12), Decimal(9), Decimal(9)]) == [
        Decimal(2),
        Decimal(-3),
        Decimal(0),
    ]


def test_summarize(conn, snapshot_id):
    _household(conn, snapshot_id)
    result = project(conn, snapshot_id, months=12, today=TODAY)
    summary = summarize(result)

    liquid = summary["liquid"]
    assert liquid["start"] == Decimal(5000)
    assert liquid["end"] == result["liquid"][-1]
    assert liquid["change"] == liquid["end"] - liquid["start"]
    assert liquid["per_month"] == liquid["change"] / 12
    assert summary["net_worth"]["end"] == result["net_worth"][-1]
    assert summary["end_label"] == "Jun 2027"

    low = summary["lowest_liquid"]
    assert low["amount"] == min(result["liquid"])

    # A $1,000 balance on a $12k/36-month loan pays off within a few months.
    assert [p["name"] for p in summary["payoffs"]] == ["Car loan"]

    # Checking sits below its $8,500 minimum for two months: one line, not two.
    assert len(result["warnings"]) == 2
    assert len(summary["warnings"]) == 1
    w = summary["warnings"][0]
    assert w["account"]["name"] == "Checking"
    assert w["months_below"] == sum(1 for x in result["warnings"])
    assert w["lowest"] == min(x["balance"] for x in result["warnings"])
    assert w["first_month_label"] == result["warnings"][0]["month_label"]


def test_month_breakdown_groups_by_holding(conn, snapshot_id):
    _household(conn, snapshot_id)
    result = project(conn, snapshot_id, months=3, today=TODAY)
    breakdown = month_breakdown(result, 0)
    assert breakdown["label"] == "Jul 2026"
    assert breakdown["money_in"] == Decimal(3000)  # salary on the 28th
    assert breakdown["autopay"] == Decimal(500)  # card paid on the 25th
    assert breakdown["money_out"] < 0  # groceries, prorated
    groups = breakdown["groups"]
    names = [g["name"] for g in groups]
    assert names[0] == "Checking"
    assert names[-1] == "(unassigned)"
    checking = groups[0]
    labels = {f["label"] for f in checking["flows"]}
    # Rent (the 1st) already passed this month; the card is paid on the 25th.
    assert labels == {"Salary", "Autopay to Visa"}
    assert checking["total"] == sum(f["amount"] for f in checking["flows"])
    unassigned = groups[-1]
    assert [f["label"] for f in unassigned["flows"]] == ["Groceries"]
    assert unassigned["total"] < 0
