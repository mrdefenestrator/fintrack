from datetime import date
from decimal import Decimal
from unittest.mock import patch

from click.testing import CliRunner

from fintrack.cli import cli


def _add_snapshot(runner, db):
    result = runner.invoke(cli, ["--db", str(db), "snapshots", "add", "main"])
    assert result.exit_code == 0, result.output


def _add_account(runner, db, name="Test Account", account_type="checking"):
    result = runner.invoke(
        cli,
        [
            "--db",
            str(db),
            "accounts",
            "add",
            "--name",
            name,
            "--institution",
            "Test Bank",
            "--type",
            account_type,
            "--balance",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output


def test_accounts_list_empty(tmp_path):
    db = tmp_path / "test.db"
    runner = CliRunner()
    _add_snapshot(runner, db)
    result = runner.invoke(cli, ["--db", str(db), "accounts", "list"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""


def test_accounts_add_and_list(tmp_path):
    db = tmp_path / "test.db"
    runner = CliRunner()
    _add_snapshot(runner, db)
    result = runner.invoke(
        cli,
        [
            "--db",
            str(db),
            "accounts",
            "add",
            "--name",
            "Chase Visa",
            "--institution",
            "Chase",
            "--type",
            "credit_card",
            "--limit",
            "5000",
            "--available",
            "4500",
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli, ["--db", str(db), "accounts", "list"])
    assert result.exit_code == 0, result.output
    assert "Chase Visa" in result.output


def test_snapshot_required_when_ambiguous(tmp_path):
    db = tmp_path / "test.db"
    runner = CliRunner()
    _add_snapshot(runner, db)
    result = runner.invoke(cli, ["--db", str(db), "snapshots", "add", "other"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli, ["--db", str(db), "accounts", "list"])
    assert result.exit_code != 0
    assert "Multiple snapshots" in result.output
    result = runner.invoke(
        cli, ["--db", str(db), "--snapshot", "main", "accounts", "list"]
    )
    assert result.exit_code == 0, result.output


def test_categories_list_seeded(tmp_path):
    db = tmp_path / "test.db"
    runner = CliRunner()
    result = runner.invoke(cli, ["--db", str(db), "categories", "list"])
    assert result.exit_code == 0, result.output
    assert "Groceries" in result.output


def test_import_ofx_and_staging_flow(tmp_path, sample_ofx):
    db = tmp_path / "test.db"
    runner = CliRunner()
    _add_snapshot(runner, db)
    _add_account(runner, db)

    with patch("fintrack.cli.ledger.classify_and_cache", return_value=(0, None)):
        result = runner.invoke(
            cli,
            ["--db", str(db), "import", str(sample_ofx), "--account", "Test Account"],
        )
    assert result.exit_code == 0, result.output
    assert "new" in result.output.lower()

    result = runner.invoke(cli, ["--db", str(db), "staging", "list"])
    assert result.exit_code == 0, result.output
    assert "1" in result.output

    result = runner.invoke(cli, ["--db", str(db), "staging", "confirm", "1"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli, ["--db", str(db), "staging", "list"])
    assert "No pending imports" in result.output


def test_balance_set_and_history(tmp_path):
    db = tmp_path / "test.db"
    runner = CliRunner()
    _add_snapshot(runner, db)
    _add_account(runner, db, name="Wallet")

    result = runner.invoke(
        cli,
        ["--db", str(db), "balance", "set", "Wallet", "42.50", "--date", "2026-06-01"],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli, ["--db", str(db), "balance", "history", "Wallet"])
    assert result.exit_code == 0, result.output
    assert "2026-06-01" in result.output
    assert "manual" in result.output


def test_report_monthly_empty(tmp_path):
    db = tmp_path / "test.db"
    runner = CliRunner()
    _add_snapshot(runner, db)
    result = runner.invoke(cli, ["--db", str(db), "report", "monthly"])
    assert result.exit_code == 0, result.output
    assert "No spending data" in result.output


def test_status_smoke(tmp_path):
    db = tmp_path / "test.db"
    runner = CliRunner()
    _add_snapshot(runner, db)
    result = runner.invoke(cli, ["--db", str(db), "status"])
    assert result.exit_code == 0, result.output
    assert "No holdings yet" in result.output
    assert "Net worth  $0  No change by " in result.output
    assert "Needs attention: All clear" in result.output
    assert "Projection warnings: All clear" in result.output


def test_status_is_the_text_overview(tmp_path):
    """status prints the Overview cards: net worth and liquid now (whole
    dollars) and their change by the end of the horizon, then the warnings."""
    db = tmp_path / "test.db"
    runner = CliRunner()
    _add_snapshot(runner, db)
    _add_account(runner, db, name="Checking")
    with patch("fintrack.cli.networth.build_overview") as build:
        build.return_value = {
            "cards": [
                "net_worth_liquid",
                "cash_flow",
                "debt_cards",
                "needs_attention",
                "spending_alerts",
                "projection_warnings",
            ],
            "months": 12,
            "has_holdings": True,
            "cash_flow": _CASH_FLOW,
            "debt_cards": _DEBT_CARDS,
            "attention": [
                {"source": "recurring", "text": "Spotify: $11.99/mo, not in budget"}
            ],
            "spending_alerts": {
                "items": [{"kind": "new_merchant", "message": "New merchant: X $75"}],
                "count": 7,
                "more": 6,
                "window_days": 30,
            },
            "summary": {
                "net_worth": _trend(Decimal("1000.40"), Decimal("1600.60")),
                "liquid": _trend(Decimal(500), Decimal(380)),
                "end_label": "Oct 2027",
                "lowest_liquid": {
                    "amount": Decimal(200),
                    "month_label": "Feb 2027",
                },
                "payoffs": [{"name": "Car loan", "month_label": "Mar 2027"}],
                "warnings": [
                    {
                        "account": {"name": "Checking", "institution": "Chase"},
                        "first_month_label": "Jan 2027",
                        "lowest": Decimal(200),
                        "minimum": Decimal(400),
                        "months_below": 2,
                    }
                ],
            },
        }
        result = runner.invoke(cli, ["--db", str(db), "status"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "Net worth  $1,000  ▲ $600 by Oct 2027"
    assert lines[1] == "Liquid       $500  ▼ $120 by Oct 2027"
    assert "Needs attention: 1 item" in lines
    assert "  Spotify: $11.99/mo, not in budget" in lines
    assert "Spending alerts: 7 things to look at" in lines
    assert "  New merchant: X $75" in lines
    assert "  +6 more (see `fintrack alerts`)" in lines
    assert "Projection warnings: 1 account dips below its minimum" in lines
    assert "  Low point $200 · Feb 2027" in lines
    assert "  Chase Checking: under $400 min from Jan 2027" in lines
    assert "  Paid off: Car loan · Mar 2027" in lines
    assert "Cash flow: 25% saved in November so far" in lines
    assert "  Income $4,000 · Spending $3,000 · Net $1,000" in lines
    assert "  12-month average: 20% saved · $1,000/mo kept on $5,000 income" in lines
    assert "Debt and cards: $9,000 owed on 1 loan" in lines
    assert "  Interest this month $45 · Payments $300/mo" in lines
    assert "  Next payoff: Car loan · Mar 2027" in lines
    assert "  Card usage 35% (over 30%) · $350 of $1,000" in lines


_DEBT_CARDS = {
    "loans": [{"name": "Car loan"}],
    "loan_owed": 9000,
    "loan_interest": 45,
    "loan_payment": 300,
    "next_payoff": {"name": "Car loan", "payoff": date(2027, 3, 15)},
    "card_usage": 0.35,
    "card_usage_high": True,
    "card_usage_high_pct": 30,
    "card_owed": 350,
    "card_limit": 1000,
}

_CASH_FLOW = {
    "month_label": "November",
    "this_month": {"income": 4000.0, "spending": 3000.0, "net": 1000.0, "rate": 25.0},
    "has_this_month": True,
    "average": {
        "income": 5000.0,
        "spending": 4000.0,
        "net": 1000.0,
        "rate": 20.0,
        "budgeted_net": None,
    },
    "avg_month_count": 10,
    "months": 12,
}


def _trend(start, end):
    return {
        "start": start,
        "end": end,
        "change": end - start,
        "per_month": (end - start) / 12,
    }


def test_report_trends_prints_cash_flow(tmp_path):
    """`report trends` ends with the Trends cash-flow footer (#99): one line
    per month, the month in progress marked, and the completed-month average."""
    from datetime import date, datetime

    db = tmp_path / "test.db"
    runner = CliRunner()
    _add_snapshot(runner, db)
    today = datetime.now().astimezone().date()
    prev = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    rows = [
        {"year": prev[0], "month": prev[1], "category": "Income", "total": 4000},
        {"year": prev[0], "month": prev[1], "category": "Housing", "total": -1000},
        {"year": prev[0], "month": prev[1], "category": "Transfer", "total": -500},
        {"year": today.year, "month": today.month, "category": "Income", "total": 100},
    ]
    with patch("fintrack.cli.ledger.get_monthly_totals_range", return_value=rows):
        result = runner.invoke(cli, ["--db", str(db), "report", "trends"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Cash flow (Transfer excluded)" in out
    prev_line = next(
        line
        for line in out.splitlines()
        if line.startswith(date(prev[0], prev[1], 1).strftime("%b %Y "))
    )
    assert prev_line.split()[2:] == [
        "$4,000.00",
        "$1,000.00",
        "$3,000.00",
        "75%",
        "$0.00",
    ]
    assert f"{today:%b %Y} (so far)" in out
    avg = out.splitlines()[-1]
    assert avg.split()[1:] == ["$4,000.00", "$1,000.00", "$3,000.00", "75%", "$0.00"]


def test_report_trends_year_prints_the_year_in_review(tmp_path):
    """`report trends --year` is the text version of Trends' Year period."""
    from datetime import date

    db = tmp_path / "test.db"
    runner = CliRunner()
    _add_snapshot(runner, db)
    review = {
        "year": 2025,
        "start": date(2025, 1, 1),
        "end": date(2025, 12, 31),
        "partial": False,
        "prev_year": 2024,
        "prev_end": date(2024, 12, 31),
        "categories": [
            {
                "category": "Groceries",
                "total": -580.0,
                "prev_total": -400.0,
                "change_pct": 45.0,
                "is_income": False,
            },
        ],
        "cash_flow": {
            "income": 4000.0,
            "spending": 630.0,
            "net": 3370.0,
            "rate": 84.25,
            "has_data": True,
        },
        "prev_cash_flow": {
            "income": 3000.0,
            "spending": 500.0,
            "net": 2500.0,
            "rate": 83.3,
            "has_data": True,
        },
        "top_merchants": [{"merchant": "Safeway", "spent": 480.0, "count": 2}],
        "net_worth": {
            "start": Decimal(1500),
            "end": Decimal(4000),
            "change": Decimal(2500),
            "covered": 1,
            "holdings": 1,
            "shows_change": True,
        },
    }
    with patch("fintrack.cli.ledger.year_review", return_value=review):
        result = runner.invoke(
            cli, ["--db", str(db), "report", "trends", "--year", "2025"]
        )
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "2025 in review"
    groceries = next(line for line in lines if line.startswith("Groceries"))
    assert groceries.split()[1:] == ["($580.00)", "($400.00)", "+45%"]
    row_2025 = next(line for line in lines if line.startswith("2025  "))
    assert row_2025.split()[1:] == ["$4,000.00", "$630.00", "$3,370.00", "84%"]
    assert "Net worth: $1,500 → $4,000 (▲ $2,500)" in lines
    assert any(line.startswith("1. Safeway") for line in lines)
