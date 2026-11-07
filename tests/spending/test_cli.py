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
            "cards": ["net_worth_liquid", "projection_warnings"],
            "months": 12,
            "has_holdings": True,
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
    assert "Projection warnings: 1 account dips below its minimum" in lines
    assert "  Low point $200 · Feb 2027" in lines
    assert "  Chase Checking: under $400 min from Jan 2027" in lines
    assert "  Paid off: Car loan · Mar 2027" in lines


def _trend(start, end):
    return {
        "start": start,
        "end": end,
        "change": end - start,
        "per_month": (end - start) / 12,
    }
