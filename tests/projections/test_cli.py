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
    # 3 month columns: current month appears as a header
    assert len(result.output.splitlines()[0].split()) >= 4


def test_project_estimate_flag(tmp_path):
    db = tmp_path / "t.db"
    assert _invoke(db, "snapshots", "add", "main").exit_code == 0
    result = _invoke(db, "project", "--months", "2", "--estimate")
    assert result.exit_code == 0
    assert "Estimated unscheduled spend" in result.output
