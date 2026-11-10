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
    assert "outside the window" in outside.output


def test_project_from_past_month(tmp_path):
    """--from in the past: actual month-ends (blank before any history), Now,
    then the projection; --month on a past month is refused."""
    from datetime import datetime

    from fintrack.projections.timeline import month_key, shift_month

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
    today = datetime.now().astimezone().date()
    current = (today.year, today.month)
    start = month_key(shift_month(current, -2))
    result = _invoke(db, "project", "--from", start, "--months", "4")
    assert result.exit_code == 0
    header = result.output.split("\n\n")[1].splitlines()[0]
    assert header.split().count("Now") == 1
    assert "Past months are actual month-end balances" in result.output
    assert "*" in result.output  # partial totals before any history

    past = _invoke(db, "project", "--from", start, "--month", start)
    assert past.exit_code != 0
    assert "in the past" in past.output

    bad = _invoke(db, "project", "--from", "2026-13")
    assert bad.exit_code != 0


def test_status_lists_stale_balances(tmp_path):
    from datetime import datetime, timedelta

    db = tmp_path / "t.db"
    old = datetime.now().astimezone().date() - timedelta(days=41)
    assert _invoke(db, "snapshots", "add", "main").exit_code == 0
    _invoke(
        db,
        "accounts",
        "add",
        "--name",
        "Chase Checking",
        "--type",
        "checking",
        "--balance",
        "1000",
        "--asOfDate",
        old.isoformat(),
    )
    output = _invoke(db, "status").output
    assert "Needs attention: 1 item" in output
    assert "  Chase Checking: last updated 41 days ago" in output
