from decimal import Decimal

import pytest
from sqlalchemy import select

from fintrack.core.models import holdings, imports, loan_details, transactions
from fintrack.ledger.importer import run_import
from fintrack.ledger.repository.accounts import add_account
from fintrack.ledger.repository.imports import confirm_import
from fintrack.networth.repository import get_asset_entries


@pytest.fixture
def sample_account_id(conn):
    return add_account(
        conn, name="Test Account", institution="Test", account_type="checking"
    )


def test_run_import_ofx_persists_ledger_balance(
    conn, sample_account_id, sample_ofx_with_balances
):
    result = run_import(conn, sample_ofx_with_balances, account_id=sample_account_id)
    row = conn.execute(
        select(imports).where(imports.c.id == result["import_id"])
    ).fetchone()
    assert row.ledger_balance == Decimal("1234.56")
    assert row.available_balance == Decimal("1184.56")


def test_run_import_venmo_csv_persists_balances(conn, sample_account_id, tmp_path):
    csv_path = tmp_path / "VenmoStatement_Test.csv"
    csv_path.write_text(
        "Account Statement,,\n"
        "Account Activity,,\n"
        ",ID,Datetime,Type,Status,Note,From,To,Amount (total),Amount (tip),"
        "Amount (tax),Amount (fee),Tax Rate,Tax Exempt,Funding Source,"
        "Destination,Beginning Balance,Ending Balance,Statement Period Venmo Fees,"
        "Terminal Location,Year to Date Venmo Fees,Disclaimer\n"
        ",,,,,,,,,,,,,,,,$10.00,,,,,,\n"
        ",1,2026-01-10T10:00:00,Payment,Complete,Lunch,Alice,Bob,+ $25.00,,0,,0,,,Venmo balance,,,,Venmo,,\n"
        ",,,,,,,,,,,,,,,,,$35.00,,,,,\n"
    )
    result = run_import(
        conn,
        csv_path,
        account_id=sample_account_id,
        configs_dir="configs/institutions",
    )
    assert result.get("error") is None, result.get("error")
    row = conn.execute(
        select(imports).where(imports.c.id == result["import_id"])
    ).fetchone()
    assert row.beginning_balance == Decimal("10.00")
    assert row.ledger_balance == Decimal("35.00")


def test_run_import_loan_ofx_stores_balance_as_owed(conn, sample_loan_ofx):
    # Issue #79: a servicer's positive LEDGERBAL is principal owed. End to end
    # (parse → stage → confirm), the canonical balance is negative and the
    # debt-side dict API presents the amount owed as positive.
    loan_id = add_account(
        conn, name="Mortgage", institution="Servicer", account_type="loan"
    )
    result = run_import(conn, sample_loan_ofx, account_id=loan_id)
    assert result.get("error") is None
    confirm_import(conn, result["import_id"])

    assert conn.execute(
        select(loan_details.c.balance).where(loan_details.c.holding_id == loan_id)
    ).scalar() == Decimal("-250000.00")
    snapshot_id = conn.execute(
        select(holdings.c.snapshot_id).where(holdings.c.id == loan_id)
    ).scalar()
    (entry,) = [e for e in get_asset_entries(conn, snapshot_id) if e["id"] == loan_id]
    assert entry["balance"] == Decimal("250000.00")


def test_reimport_with_changed_descriptions_skips_by_bank_id(
    conn, sample_account_id, write_ofx
):
    # Issue #82: a re-export renames merchants; the FITIDs still match, so the
    # second import adds nothing even though no fingerprint matches.
    first = write_ofx(
        [
            ("A1", "20261001", "-12.34", "APPLE.COM/BILL"),
            ("A2", "20261002", "-56.78", "UBER *TRIP"),
        ]
    )
    second = write_ofx(
        [
            ("A1", "20261001", "-12.34", "Apple Services"),
            ("A2", "20261002", "-56.78", "Uber Trip Help.Uber.Com"),
            ("A3", "20261003", "-9.99", "Netflix"),
        ]
    )
    run_import(conn, first, account_id=sample_account_id)
    result = run_import(conn, second, account_id=sample_account_id)

    assert result["new_count"] == 1
    assert result["skipped_count"] == 2
    stored = conn.execute(
        select(transactions.c.external_id).where(
            transactions.c.import_id == result["import_id"]
        )
    ).scalars()
    assert list(stored) == ["A3"]


def test_bank_id_match_does_not_cross_accounts(conn, sample_account_id, write_ofx):
    other = add_account(
        conn, name="Other Account", institution="Test", account_type="checking"
    )
    run_import(
        conn,
        write_ofx([("A1", "20261001", "-12.34", "SHOP")]),
        account_id=sample_account_id,
    )
    result = run_import(
        conn, write_ofx([("A1", "20261001", "-1.00", "ELSE")]), account_id=other
    )
    assert result["new_count"] == 1
