import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from spending.types import ImportResult, ParsedTransaction

_MAX_HEADER_SCAN = 10


def _parse_signed_dollar(value: str) -> Decimal:
    """Parse amounts like '+ $46.74' or '- $208.85'."""
    value = value.strip()
    negative = value.startswith("-")
    cleaned = value.lstrip("+-").replace("$", "").replace(",", "").strip()
    return -Decimal(cleaned) if negative else Decimal(cleaned)


def parse_csv(file_path: str | Path, config_path: str | Path) -> ImportResult:
    with open(config_path) as f:
        config = yaml.safe_load(f)

    date_col = config["date_column"]
    amount_col = config["amount_column"]
    desc_col = config["description_column"]
    date_fmt = config["date_format"]
    header_row = config.get("header_row", 0)
    amount_format = config.get("amount_format", "standard")
    beg_bal_col = config.get("beginning_balance_column")
    end_bal_col = config.get("ending_balance_column")

    transactions: list[ParsedTransaction] = []

    with open(file_path, newline="") as f:
        for _ in range(header_row):
            f.readline()
        all_rows = list(csv.DictReader(f))

    for row in all_rows:
        raw_amount = row.get(amount_col, "").strip()
        if not raw_amount:
            continue
        try:
            if amount_format == "signed_dollar":
                amount = _parse_signed_dollar(raw_amount)
            else:
                amount = Decimal(raw_amount.replace(",", ""))
        except (InvalidOperation, ValueError):
            continue

        raw_date = row.get(date_col, "").strip()
        if not raw_date:
            continue
        try:
            txn_date = datetime.strptime(raw_date, date_fmt).date()
        except ValueError:
            continue

        note = row.get(desc_col, "").strip()
        type_col = config.get("type_column")
        debit_party_col = config.get("debit_party_column")
        credit_party_col = config.get("credit_party_column")

        if type_col or debit_party_col or credit_party_col:
            txn_type = row.get(type_col, "").strip() if type_col else ""
            party_col = debit_party_col if amount < 0 else credit_party_col
            party = row.get(party_col, "").strip() if party_col else ""
            if txn_type and note:
                description = f"{txn_type}: {note}"
                if party:
                    description += f" ({party})"
            elif txn_type:
                description = f"{txn_type} ({party})" if party else txn_type
            elif note:
                description = f"{note} ({party})" if party else note
            else:
                description = party
        else:
            description = note

        transactions.append(
            ParsedTransaction(
                date=txn_date,
                amount=amount,
                raw_description=description,
            )
        )

    beginning_balance = None
    ledger_balance = None

    if beg_bal_col:
        for row in all_rows:
            raw = row.get(beg_bal_col, "").strip()
            if raw:
                try:
                    beginning_balance = _parse_signed_dollar(raw)
                except (InvalidOperation, ValueError):
                    pass
                break

    if end_bal_col:
        for row in all_rows:
            raw = row.get(end_bal_col, "").strip()
            if raw:
                try:
                    ledger_balance = _parse_signed_dollar(raw)
                except (InvalidOperation, ValueError):
                    pass
                break

    return ImportResult(
        transactions=transactions,
        account_name=config.get("account_name"),
        beginning_balance=beginning_balance,
        ledger_balance=ledger_balance,
    )


def detect_institution_config(
    csv_path: str | Path, configs_dir: str | Path
) -> str | None:
    rows: list[list[str]] = []
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        for _ in range(_MAX_HEADER_SCAN):
            try:
                rows.append([h.strip() for h in next(reader)])
            except StopIteration:
                break

    configs_dir = Path(configs_dir)
    for config_file in configs_dir.glob("*.yaml"):
        with open(config_file) as f:
            config = yaml.safe_load(f)

        header_row = config.get("header_row", 0)
        pattern = config.get("header_pattern", [])
        if (
            pattern
            and header_row < len(rows)
            and all(col in rows[header_row] for col in pattern)
        ):
            return str(config_file)

    return None
