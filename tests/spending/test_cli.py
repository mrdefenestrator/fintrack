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
    assert "Net worth: $0.00" in result.output
    assert "Projection warnings:" in result.output


def test_status_is_the_text_overview(tmp_path):
    """status prints the Overview cards: net worth and liquid now plus their
    12-month projection, then the projection warnings."""
    db = tmp_path / "test.db"
    runner = CliRunner()
    _add_snapshot(runner, db)
    _add_account(runner, db, name="Checking")
    with patch("fintrack.cli.networth.build_overview") as build:
        build.return_value = {
            "cards": ["net_worth", "liquid", "projection_warnings"],
            "months": 12,
            "has_holdings": True,
            "summary": {
                "net_worth": _trend(1000, 1600),
                "liquid": _trend(500, 380),
                "end_label": "Oct 2027",
                "lowest_liquid": {
                    "amount": 200,
                    "month_index": 3,
                    "month_label": "Feb 2027",
                },
                "payoffs": [
                    {"name": "Car loan", "month_index": 4, "month_label": "Mar 2027"}
                ],
                "warnings": [
                    {
                        "account": {"name": "Checking"},
                        "first_month_index": 2,
                        "first_month_label": "Jan 2027",
                        "lowest": 200,
                        "minimum": 400,
                        "months_below": 2,
                    }
                ],
            },
        }
        result = runner.invoke(cli, ["--db", str(db), "status"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "No holdings yet" not in out
    assert "Net worth: $1,000.00" in out
    assert "+$600.00 projected over the next 12 months, to $1,600.00 by Oct 2027" in out
    assert "Liquid: $500.00" in out
    assert "-$120.00 projected" in out
    assert "Lowest liquid: $200.00 in Feb 2027" in out
    assert "Checking drops below its $400.00 minimum in Jan 2027" in out
    assert "No account drops below" not in out
    assert "Paid off: Car loan (Mar 2027)" in out


def _trend(start, end):
    return {
        "start": start,
        "end": end,
        "change": end - start,
        "per_month": (end - start) / 12,
    }
