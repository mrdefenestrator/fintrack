from decimal import Decimal

import pytest
from sqlalchemy import select

from spending.importer import run_import
from spending.models import imports
from spending.repository.accounts import add_account


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
