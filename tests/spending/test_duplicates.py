"""Resolving possible-duplicate flags: repository, web staging review, CLI
(issue #82)."""

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine, select

from fintrack.cli import cli
from fintrack.core.models import transaction_duplicates, transactions
from fintrack.ledger.repository.duplicates import (
    get_duplicate,
    list_duplicates,
    resolve_duplicate,
)
from fintrack.ledger.repository.transactions import get_transactions
from fintrack.snapshots.repository import create_snapshot, get_snapshot_id

# --- repository -----------------------------------------------------------


def test_resolve_and_undo(conn, stage_pair):
    _, copy_id = stage_pair(conn)
    [flag] = list_duplicates(conn)
    assert flag["transaction_id"] == copy_id
    assert flag["of_raw_description"] == "UBER *TRIP HELP.UBER.COM"

    resolve_duplicate(conn, copy_id, "duplicate")
    assert get_duplicate(conn, copy_id)["status"] == "duplicate"
    assert get_duplicate(conn, copy_id)["resolved_at"] is not None
    assert list_duplicates(conn) == []

    resolve_duplicate(conn, copy_id, "flagged")
    assert get_duplicate(conn, copy_id)["resolved_at"] is None
    assert len(list_duplicates(conn)) == 1


def test_resolve_rejects_unknown_flag_and_status(conn, stage_pair):
    _, copy_id = stage_pair(conn)
    with pytest.raises(LookupError):
        resolve_duplicate(conn, copy_id + 100, "duplicate")
    with pytest.raises(ValueError):
        resolve_duplicate(conn, copy_id, "deleted")


def test_resolve_is_snapshot_scoped(conn, stage_pair):
    _, copy_id = stage_pair(conn)
    other = create_snapshot(conn, "other")
    with pytest.raises(LookupError):
        resolve_duplicate(conn, copy_id, "duplicate", snapshot_id=other)
    assert list_duplicates(conn, other) == []


def test_rejecting_the_original_import_clears_the_flag(conn, stage_pair):
    from fintrack.ledger.repository.imports import reject_import

    _, copy_id = stage_pair(conn)
    original_import = conn.execute(
        select(transactions.c.import_id).where(
            transactions.c.id == get_duplicate(conn, copy_id)["duplicate_of_id"]
        )
    ).scalar_one()
    reject_import(conn, original_import)
    assert conn.execute(select(transaction_duplicates)).fetchall() == []


# --- CLI -------------------------------------------------------------------


def test_cli_list_mark_dismiss_undo(tmp_path, stage_pair):
    db = tmp_path / "cli.db"
    runner = CliRunner()

    def run(*args):
        result = runner.invoke(cli, ["--db", str(db), *args])
        assert result.exit_code == 0, result.output
        return result.output

    run("snapshots", "add", "main")
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        import_id, copy_id = stage_pair(conn, get_snapshot_id(conn, "main"))
    run("staging", "confirm", str(import_id - 1))
    run("staging", "confirm", str(import_id))

    out = run("duplicates", "list")
    assert "Uber Trip Help.Uber.Com" in out and "1 day apart" in out

    assert "a duplicate" in run("duplicates", "mark", str(copy_id))
    assert "No possible duplicates." in run("duplicates", "list")
    assert "duplicate" in run("duplicates", "list", "--all")
    with engine.connect() as conn:
        assert len(get_transactions(conn)) == 1

    run("duplicates", "undo", str(copy_id))
    run("duplicates", "dismiss", str(copy_id))
    with engine.connect() as conn:
        assert len(get_transactions(conn)) == 2
        assert get_duplicate(conn, copy_id)["status"] == "not_duplicate"

    result = runner.invoke(cli, ["--db", str(db), "duplicates", "mark", "9999"])
    assert result.exit_code != 0
    assert "has no duplicate flag" in result.output
