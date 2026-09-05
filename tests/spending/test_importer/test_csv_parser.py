from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from fintrack.core.config import INSTITUTIONS_DIR
from fintrack.ledger.importer.csv_parser import (
    _parse_signed_dollar,
    detect_institution_config,
    parse_csv,
)


def test_parse_csv(sample_csv, tmp_path):
    config_path = tmp_path / "inst.yaml"
    config_path.write_text(
        """
name: Test Bank
institution: Test
date_column: "Transaction Date"
amount_column: "Amount"
description_column: "Description"
date_format: "%m/%d/%Y"
account_name: "Test Card"
header_pattern:
  - "Transaction Date"
  - "Post Date"
  - "Description"
  - "Category"
  - "Type"
  - "Amount"
"""
    )
    result = parse_csv(sample_csv, str(config_path))
    assert len(result["transactions"]) == 4
    assert result["account_name"] == "Test Card"


def test_parse_csv_amounts(sample_csv, tmp_path):
    config_path = tmp_path / "inst.yaml"
    config_path.write_text(
        """
name: Test Bank
institution: Test
date_column: "Transaction Date"
amount_column: "Amount"
description_column: "Description"
date_format: "%m/%d/%Y"
account_name: null
header_pattern: []
"""
    )
    result = parse_csv(sample_csv, str(config_path))
    txn = result["transactions"][0]
    assert txn["date"] == date(2024, 1, 15)
    assert txn["amount"] == Decimal("-42.50")
    assert txn["raw_description"] == "WHOLE FOODS MARKET #10234"


def test_detect_institution_config(sample_csv, tmp_path):
    config_dir = tmp_path / "institutions"
    config_dir.mkdir()
    config_file = config_dir / "test.yaml"
    config_file.write_text(
        """
name: Test Bank
institution: Test
date_column: "Transaction Date"
amount_column: "Amount"
description_column: "Description"
date_format: "%m/%d/%Y"
account_name: null
header_pattern:
  - "Transaction Date"
  - "Post Date"
  - "Description"
  - "Category"
  - "Type"
  - "Amount"
"""
    )
    detected = detect_institution_config(sample_csv, str(config_dir))
    assert detected is not None
    assert "Test Bank" in Path(detected).read_text()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("+ $46.74", Decimal("46.74")),
        ("- $208.85", Decimal("-208.85")),
        ("+ $1,234.56", Decimal("1234.56")),
        ("- $0.50", Decimal("-0.50")),
        ("($2,613.09)", Decimal("-2613.09")),
        ("($0.00)", Decimal("0.00")),
    ],
)
def test_parse_signed_dollar(value, expected):
    assert _parse_signed_dollar(value) == expected


def test_parse_csv_venmo(sample_venmo_csv, tmp_path):
    config_path = tmp_path / "venmo.yaml"
    config_path.write_text(
        """
name: Venmo
institution: Venmo
header_row: 2
date_column: "Datetime"
date_format: "%Y-%m-%dT%H:%M:%S"
amount_column: "Amount (total)"
amount_format: signed_dollar
description_column: "Note"
type_column: "Type"
debit_party_column: "To"
credit_party_column: "From"
account_name: null
header_pattern:
  - "ID"
  - "Datetime"
  - "Amount (total)"
  - "From"
"""
    )
    result = parse_csv(sample_venmo_csv, str(config_path))
    # 2 payments (credits) + 1 standard transfer (debit); opening/footer rows skipped
    assert len(result["transactions"]) == 3
    txns = result["transactions"]
    assert txns[0]["date"] == date(2026, 4, 3)
    assert txns[0]["amount"] == Decimal("46.74")
    # credits: other party comes from "From" column
    assert txns[0]["raw_description"] == "Payment: March phone bill (Alice Smith)"
    assert txns[1]["raw_description"] == "Payment: Dinner (Bob Jones)"
    # debit transfer: To is empty, so no party suffix
    assert txns[2]["amount"] == Decimal("-66.74")
    assert txns[2]["raw_description"] == "Standard Transfer"


def test_detect_institution_config_venmo(sample_venmo_csv, tmp_path):
    config_dir = tmp_path / "institutions"
    config_dir.mkdir()
    config_file = config_dir / "venmo.yaml"
    config_file.write_text(
        """
name: Venmo
institution: Venmo
header_row: 2
date_column: "Datetime"
date_format: "%Y-%m-%dT%H:%M:%S"
amount_column: "Amount (total)"
amount_format: signed_dollar
description_column: "Note"
type_column: "Type"
debit_party_column: "To"
credit_party_column: "From"
account_name: null
header_pattern:
  - "ID"
  - "Datetime"
  - "Amount (total)"
  - "From"
"""
    )
    detected = detect_institution_config(sample_venmo_csv, str(config_dir))
    assert detected is not None
    assert "Venmo" in Path(detected).read_text()


_VENMO_CONFIG_WITH_BALANCES = """
name: Venmo
institution: Venmo
header_row: 2
date_column: "Datetime"
date_format: "%Y-%m-%dT%H:%M:%S"
amount_column: "Amount (total)"
amount_format: signed_dollar
description_column: "Note"
type_column: "Type"
debit_party_column: "To"
credit_party_column: "From"
account_name: null
header_pattern:
  - "ID"
  - "Datetime"
  - "Amount (total)"
  - "From"
beginning_balance_column: "Beginning Balance"
ending_balance_column: "Ending Balance"
"""


def test_parse_csv_venmo_beginning_balance(sample_venmo_csv, tmp_path):
    config_path = tmp_path / "venmo_bal.yaml"
    config_path.write_text(_VENMO_CONFIG_WITH_BALANCES)
    result = parse_csv(sample_venmo_csv, str(config_path))
    assert result.get("beginning_balance") == Decimal("0.00")


def test_parse_csv_venmo_ending_balance(sample_venmo_csv, tmp_path):
    config_path = tmp_path / "venmo_bal.yaml"
    config_path.write_text(_VENMO_CONFIG_WITH_BALANCES)
    result = parse_csv(sample_venmo_csv, str(config_path))
    assert result.get("ledger_balance") == Decimal("0.00")


def test_parse_csv_no_balance_columns_returns_none(sample_csv, tmp_path):
    config_path = tmp_path / "no_bal.yaml"
    config_path.write_text(
        """
name: Test Bank
institution: Test
date_column: "Transaction Date"
amount_column: "Amount"
description_column: "Description"
date_format: "%m/%d/%Y"
account_name: null
header_pattern: []
"""
    )
    result = parse_csv(sample_csv, str(config_path))
    assert result.get("beginning_balance") is None
    assert result.get("ledger_balance") is None


def test_detect_institution_config_no_match(sample_csv, tmp_path):
    config_dir = tmp_path / "institutions"
    config_dir.mkdir()
    config_file = config_dir / "other.yaml"
    config_file.write_text(
        """
name: Other Bank
institution: Other
date_column: "Date"
amount_column: "Amt"
description_column: "Desc"
date_format: "%Y-%m-%d"
account_name: null
header_pattern:
  - "Date"
  - "Amt"
  - "Desc"
"""
    )
    detected = detect_institution_config(sample_csv, str(config_dir))
    assert detected is None


def test_parse_csv_rocket_mortgage(sample_rocket_csv):
    config_path = INSTITUTIONS_DIR / "rocketmortgage.yaml"
    result = parse_csv(sample_rocket_csv, str(config_path))
    txns = result["transactions"]
    # Escrow Adjustment rows are filtered out; only Monthly Payments remain.
    assert len(txns) == 2
    assert txns[0]["date"] == date(2026, 8, 10)
    # Total is an outflow; negate makes it a ledger expense.
    assert txns[0]["amount"] == Decimal("-3023.41")
    assert txns[0]["raw_description"] == "Monthly Payment"


def test_detect_institution_config_rocket_mortgage(sample_rocket_csv):
    detected = detect_institution_config(sample_rocket_csv, str(INSTITUTIONS_DIR))
    assert detected is not None
    assert "Rocket Mortgage" in Path(detected).read_text()
