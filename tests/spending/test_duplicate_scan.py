"""Scanning confirmed data for possible duplicates (issue #82)."""

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine, delete, select

from fintrack.cli import cli
from fintrack.core.models import transaction_duplicates, transactions
from fintrack.ledger.duplicate_scan import scan_for_duplicates
from fintrack.ledger.importer import run_import
from fintrack.ledger.repository.accounts import add_account
from fintrack.ledger.repository.duplicates import list_duplicates, resolve_duplicate
from fintrack.ledger.repository.imports import confirm_import
from fintrack.ledger.repository.transactions import get_transactions
from fintrack.snapshots.repository import get_snapshot_id


@pytest.fixture
def legacy_copies(write_ofx):
    """Factory: two confirmed overlapping exports imported before flagging
    existed (flags wiped). Returns {raw_description: transaction id}."""

    def _seed(conn, snapshot_id=None):
        account_id = add_account(
            conn,
            name="Card",
            institution="Apple",
            account_type="checking",
            snapshot_id=snapshot_id,
        )
        for rows in (
            [
                ("A1", "20261001", "-56.78", "UBER *TRIP"),
                ("A2", "20261002", "-9.99", "NETFLIX.COM"),
            ],
            [
                ("B1", "20261002", "-56.78", "Uber Trip"),
                ("B2", "20261002", "-9.99", "Netflix.com"),
                ("B3", "20261003", "-20.00", "Shell Oil"),
            ],
        ):
            result = run_import(conn, write_ofx(rows), account_id=account_id)
            confirm_import(conn, result["import_id"])
        conn.execute(delete(transaction_duplicates))
        conn.commit()
        return {
            r.raw_description: r.id
            for r in conn.execute(
                select(transactions.c.raw_description, transactions.c.id)
            )
        }

    return _seed


def test_scan_dry_run_finds_later_copies_without_writing(conn, legacy_copies):
    ids = legacy_copies(conn)
    found = scan_for_duplicates(conn)
    assert [(f["txn"]["id"], f["of"]["id"]) for f in found] == [
        (ids["Uber Trip"], ids["UBER *TRIP"]),
        (ids["Netflix.com"], ids["NETFLIX.COM"]),
    ]
    assert found[0]["reason"].startswith("same amount · 1 day apart")
    assert conn.execute(select(transaction_duplicates)).fetchall() == []


def test_scan_apply_is_idempotent(conn, legacy_copies):
    legacy_copies(conn)
    assert len(scan_for_duplicates(conn, apply=True)) == 2
    assert {f["source"] for f in list_duplicates(conn)} == {"scan"}
    assert scan_for_duplicates(conn, apply=True) == []
    assert len(list_duplicates(conn)) == 2


def test_dismissals_stick_across_scans(conn, legacy_copies):
    ids = legacy_copies(conn)
    scan_for_duplicates(conn, apply=True)
    resolve_duplicate(conn, ids["Uber Trip"], "not_duplicate")
    resolve_duplicate(conn, ids["Netflix.com"], "duplicate")
    assert scan_for_duplicates(conn) == []


def test_scan_is_snapshot_scoped(conn, legacy_copies):
    from fintrack.snapshots.repository import create_snapshot

    legacy_copies(conn)
    other = create_snapshot(conn, "other")
    assert scan_for_duplicates(conn, other) == []


# --- CLI -------------------------------------------------------------------


def test_cli_scan_dry_run_then_apply(tmp_path, legacy_copies):
    db = tmp_path / "cli.db"
    runner = CliRunner()

    def run(*args):
        result = runner.invoke(cli, ["--db", str(db), *args])
        assert result.exit_code == 0, result.output
        return result.output

    run("snapshots", "add", "main")
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        legacy_copies(conn, get_snapshot_id(conn, "main"))

    out = run("duplicates", "scan")
    assert "2 possible duplicate(s)." in out and "Dry run" in out
    assert "No possible duplicates." in run("duplicates", "list")

    assert "Flagged for review" in run("duplicates", "scan", "--apply")
    assert "Uber Trip" in run("duplicates", "list")
    assert "No new possible duplicates found." in run("duplicates", "scan")


# --- Transactions sheet filter ---------------------------------------------


def test_status_filters(conn, legacy_copies):
    ids = legacy_copies(conn)
    scan_for_duplicates(conn, apply=True)
    resolve_duplicate(conn, ids["Netflix.com"], "duplicate")

    [possible] = get_transactions(conn, status="possible_duplicate")
    assert possible["id"] == ids["Uber Trip"]
    assert possible["dup_of_description"] == "UBER *TRIP"

    [marked] = get_transactions(conn, status="duplicate")
    assert marked["id"] == ids["Netflix.com"]
    assert len(get_transactions(conn)) == 4  # the marked copy is hidden
