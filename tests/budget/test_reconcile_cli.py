"""CLI tests for dismissing link suggestions (issue #70): `transactions
dismiss` / `undismiss` and the `d` choice in `transactions suggest --apply`."""

from datetime import date

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from fintrack.accounts.repository import add_account
from fintrack.cli import cli
from fintrack.core.db import get_engine, init_db
from fintrack.core.models import transactions
from fintrack.ledger.repository.corrections import get_correction
from fintrack.snapshots.repository import create_snapshot
from tests.budget.conftest import TxnSeeder, make_entry


@pytest.fixture
def db(tmp_path):
    """A DB file with one snapshot, a Rent entry (index 0), and one matching
    unlinked charge. Returns (path, txn_id)."""
    path = tmp_path / "test.db"
    engine = get_engine(str(path))
    init_db(engine)
    with engine.connect() as conn:
        sid = create_snapshot(conn, "home")
        acct = add_account(conn, sid, {"name": "Checking", "type": "checking"})
        make_entry(conn, sid, description="Rent", amount=2000, category="Housing")
        txn_id = TxnSeeder(conn, acct).add(
            date(2026, 5, 1), "-2000.00", "Landlord", "Housing"
        )
    engine.dispose()
    return path, txn_id


def _run(path, *args, input=None):
    result = CliRunner().invoke(
        cli, ["--db", str(path), "transactions", *args], input=input
    )
    assert result.exit_code == 0, result.output
    return result.output


def test_dismiss_then_undismiss(db):
    path, txn_id = db
    assert "1 suggestion(s)" in _run(path, "suggest")
    assert "Dismissed suggestion" in _run(path, "dismiss", str(txn_id), "0")
    assert "No suggestions." in _run(path, "suggest")
    assert "Cleared 1" in _run(path, "undismiss", str(txn_id))
    assert "1 suggestion(s)" in _run(path, "suggest")


def test_dismiss_rejects_bad_index(db):
    path, txn_id = db
    result = CliRunner().invoke(
        cli, ["--db", str(path), "transactions", "dismiss", str(txn_id), "5"]
    )
    assert result.exit_code != 0
    assert "out of range" in result.output


def test_suggest_apply_dismiss_choice(db):
    path, txn_id = db
    out = _run(path, "suggest", "--apply", input="d\n")
    assert "Applied 0 of 1 suggestion(s), dismissed 1." in out
    assert "No suggestions." in _run(path, "suggest")
    engine = get_engine(str(path))
    with engine.connect() as conn:
        assert get_correction(conn, txn_id) is None
    engine.dispose()


def test_suggest_apply_yes_links(db):
    path, txn_id = db
    out = _run(path, "suggest", "--apply", input="y\n")
    assert "Applied 1 of 1 suggestion(s)." in out
    engine = get_engine(str(path))
    with engine.connect() as conn:
        assert get_correction(conn, txn_id)["budget_entry_ref"] is not None
    engine.dispose()


def test_budget_actual_shows_reversed_column(db):
    path, txn_id = db
    engine = get_engine(str(path))
    with engine.connect() as conn:
        acct = conn.execute(select(transactions.c.account_id)).scalar()
        refund = TxnSeeder(conn, acct).add(date(2026, 5, 20), "200.00", "Landlord")
    engine.dispose()
    _run(path, "link", str(txn_id), "0")
    _run(path, "link", str(refund), "0")
    out = _run(path, "budget-actual", "--year", "2026", "--month", "5")
    assert "Reversed" in out
    assert "$1,800.00" in out
    assert "($200.00)" in out
