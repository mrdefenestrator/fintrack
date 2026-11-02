"""CLI smoke tests for `fintrack project`."""

from click.testing import CliRunner

from fintrack.cli import cli


def _invoke(db_path, *args):
    runner = CliRunner()
    return runner.invoke(cli, ["--db", str(db_path), *args], catch_exceptions=False)


def test_project_smoke(tmp_path):
    db = tmp_path / "t.db"
    assert _invoke(db, "snapshots", "add", "main").exit_code == 0
    assert (
        _invoke(
            db,
            "accounts",
            "add",
            "--name",
            "Checking",
            "--type",
            "checking",
            "--balance",
            "1000",
        ).exit_code
        == 0
    )

    result = _invoke(db, "project", "--months", "3")
    assert result.exit_code == 0
    assert "Checking" in result.output
    assert "Liquid total" in result.output
    assert "Net worth" in result.output
    assert "Liquid by" in result.output
    assert "now $1,000.00" in result.output
    assert "Lowest liquid: $1,000.00" in result.output
    # 3 month columns: current month appears as a header
    assert len(result.output.splitlines()[0].split()) >= 4


def test_project_estimate_flag(tmp_path):
    db = tmp_path / "t.db"
    assert _invoke(db, "snapshots", "add", "main").exit_code == 0
    result = _invoke(db, "project", "--months", "2", "--estimate")
    assert result.exit_code == 0
    assert "Estimated unscheduled spend" in result.output


def test_project_changes_flag(tmp_path):
    db = tmp_path / "t.db"
    assert _invoke(db, "snapshots", "add", "main").exit_code == 0
    assert (
        _invoke(
            db,
            "accounts",
            "add",
            "--name",
            "Checking",
            "--type",
            "checking",
            "--balance",
            "1000",
        ).exit_code
        == 0
    )
    result = _invoke(db, "project", "--months", "2", "--changes")
    assert result.exit_code == 0
    assert "Liquid change" in result.output
    assert "Net worth change" in result.output
    header = next(
        line for line in result.output.splitlines() if line.startswith("Account")
    )
    assert "Now" not in header


def test_project_month_breakdown(tmp_path):
    from datetime import date, datetime

    db = tmp_path / "t.db"
    assert _invoke(db, "snapshots", "add", "main").exit_code == 0
    today = datetime.now().astimezone().date()
    next_month = date(today.year + today.month // 12, today.month % 12 + 1, 1)
    result = _invoke(
        db, "project", "--months", "3", "--month", next_month.strftime("%Y-%m")
    )
    assert result.exit_code == 0
    assert f"What happens in {next_month:%b %Y}" in result.output
    assert "Money in" in result.output
    # The table is replaced by the breakdown.
    assert "Liquid total" not in result.output

    outside = _invoke(db, "project", "--months", "3", "--month", "1999-01")
    assert outside.exit_code != 0
    assert "outside the projection" in outside.output
