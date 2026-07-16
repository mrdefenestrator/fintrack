from unittest.mock import patch

from click.testing import CliRunner

from spending.cli import cli


def test_accounts_list_empty(tmp_path):
    db = tmp_path / "test.db"
    runner = CliRunner()
    result = runner.invoke(cli, ["--db", str(db), "accounts", "list"])
    assert result.exit_code == 0
    assert "No accounts" in result.output


def test_accounts_add_and_list(tmp_path):
    db = tmp_path / "test.db"
    runner = CliRunner()
    runner.invoke(
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
        ],
    )
    result = runner.invoke(cli, ["--db", str(db), "accounts", "list"])
    assert result.exit_code == 0
    assert "Chase Visa" in result.output


def test_categories_list_seeded(tmp_path):
    db = tmp_path / "test.db"
    runner = CliRunner()
    result = runner.invoke(cli, ["--db", str(db), "categories", "list"])
    assert result.exit_code == 0
    assert "Groceries" in result.output


def test_import_ofx(tmp_path, sample_ofx):
    db = tmp_path / "test.db"
    runner = CliRunner()
    runner.invoke(
        cli,
        [
            "--db",
            str(db),
            "accounts",
            "add",
            "--name",
            "Test Account",
            "--institution",
            "Test",
            "--type",
            "checking",
        ],
    )

    with patch("spending.cli.classify_and_cache", return_value=(0, None)):
        result = runner.invoke(
            cli,
            [
                "--db",
                str(db),
                "import",
                str(sample_ofx),
                "--account",
                "Test Account",
            ],
        )

    assert result.exit_code == 0
    assert "imported" in result.output.lower() or "new" in result.output.lower()


def test_status_empty(tmp_path):
    db = tmp_path / "test.db"
    runner = CliRunner()
    result = runner.invoke(cli, ["--db", str(db), "status"])
    assert result.exit_code == 0
    assert "No spending data" in result.output
