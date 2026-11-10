"""`fintrack recurring` (issue #100): list, add, link, dismiss, undismiss."""

from datetime import datetime, timedelta

import pytest
from click.testing import CliRunner

from fintrack.accounts.repository import add_account
from fintrack.budget.repository import get_budget_entries
from fintrack.cli import cli
from fintrack.core.db import get_engine, init_db
from fintrack.snapshots.repository import create_snapshot
from tests.budget.conftest import TxnSeeder, make_entry


@pytest.fixture
def db(tmp_path):
    """Monthly Spotify (not budgeted) and Gym Co (entry 0 names it)."""
    path = tmp_path / "test.db"
    engine = get_engine(str(path))
    init_db(engine)
    today = datetime.now().astimezone().date()
    with engine.connect() as conn:
        sid = create_snapshot(conn, "home")
        acct = add_account(conn, sid, {"name": "Checking", "type": "checking"})
        seeder = TxnSeeder(conn, acct)
        for i in range(6):
            seeder.add(today - timedelta(days=30 * i + 2), "-11.99", "Spotify", "Fun")
            seeder.add(today - timedelta(days=30 * i + 4), "-40.00", "Gym Co", "Health")
        make_entry(conn, sid, description="Gym Co membership", amount=40)
    engine.dispose()
    return path


def _run(path, *args):
    result = CliRunner().invoke(cli, ["--db", str(path), "recurring", *args])
    assert result.exit_code == 0, result.output
    return result.output


def _entries(path):
    engine = get_engine(str(path))
    with engine.connect() as conn:
        out = get_budget_entries(conn, 1)
    engine.dispose()
    return out


def test_list_shows_what_needs_attention(db):
    out = _run(db)
    assert "Spotify" in out and "not in budget" in out
    assert "Gym Co" not in out
    assert "Gym Co" in _run(db, "list", "--all")


def test_add_creates_and_links(db):
    out = _run(db, "add", "Spotify")
    assert "Added 'Spotify'" in out and "linked 6" in out
    assert [e["description"] for e in _entries(db)] == ["Gym Co membership", "Spotify"]
    assert "No recurring charges need attention" in _run(db)


def test_link_to_existing_entry(db):
    out = _run(db, "link", "Gym Co", "0")
    assert "Linked 6 transaction(s)" in out
    assert "✓ in budget" in _run(db, "list", "--all")


def test_dismiss_and_undismiss(db):
    _run(db, "dismiss", "Spotify")
    assert "Spotify" not in _run(db)
    assert "Spotify" in _run(db, "--dismissed")
    assert "will show again" in _run(db, "undismiss", "Spotify")
    assert "wasn't dismissed" in _run(db, "undismiss", "Spotify")


def test_unknown_merchant_errors(db):
    result = CliRunner().invoke(cli, ["--db", str(db), "recurring", "add", "Nobody"])
    assert result.exit_code != 0
    assert "No recurring expense" in result.output
