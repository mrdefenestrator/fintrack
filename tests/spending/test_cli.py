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
    assert "Total" in result.output
