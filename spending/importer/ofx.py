from decimal import Decimal
from pathlib import Path

from ofxparse import OfxParser

from spending.types import AccountMeta, ImportResult, ParsedTransaction

_ACCOUNT_TYPE_MAP = {
    "CHECKING": "checking",
    "SAVINGS": "savings",
    "MONEYMRKT": "savings",
    "CREDITLINE": "credit_card",
    "CREDITCARD": "credit_card",
}

_ACCOUNT_TYPE_DISPLAY = {
    "checking": "Checking",
    "savings": "Savings",
    "credit_card": "Credit Card",
}


def parse_ofx(file_path: str | Path) -> ImportResult:
    with open(file_path, "rb") as f:
        ofx = OfxParser.parse(f)

    transactions: list[ParsedTransaction] = []
    ledger_balance = None
    ledger_balance_date = None
    available_balance = None
    available_balance_date = None

    account = ofx.account
    if account and account.statement:
        stmt = account.statement
        for txn in stmt.transactions:
            transactions.append(
                ParsedTransaction(
                    date=txn.date.date() if hasattr(txn.date, "date") else txn.date,
                    amount=Decimal(str(txn.amount)),
                    raw_description=txn.payee or txn.memo or "",
                )
            )
        if getattr(stmt, "balance", None) is not None:
            ledger_balance = Decimal(str(stmt.balance))
        if getattr(stmt, "balance_date", None) is not None:
            d = stmt.balance_date
            ledger_balance_date = d.date() if hasattr(d, "date") else d
        if getattr(stmt, "available_balance", None) is not None:
            available_balance = Decimal(str(stmt.available_balance))
        if getattr(stmt, "available_balance_date", None) is not None:
            d = stmt.available_balance_date
            available_balance_date = d.date() if hasattr(d, "date") else d

    return ImportResult(
        transactions=transactions,
        account_name=None,
        ledger_balance=ledger_balance,
        ledger_balance_date=ledger_balance_date,
        available_balance=available_balance,
        available_balance_date=available_balance_date,
    )


def extract_ofx_metadata(file_path: str | Path) -> AccountMeta | None:
    """Parse OFX institution/account metadata without importing transactions.

    Returns None on any parse failure so callers degrade gracefully.
    """
    try:
        with open(file_path, "rb") as f:
            ofx = OfxParser.parse(f)

        account = ofx.account
        if not account:
            return None

        institution = ""
        if account.institution and account.institution.organization:
            institution = account.institution.organization

        raw_type = (account.account_type or "").upper()
        account_type = _ACCOUNT_TYPE_MAP.get(raw_type, "other")

        account_id = account.account_id or ""
        last4 = account_id[-4:] if len(account_id) >= 4 else account_id

        parts = []
        if institution:
            parts.append(institution)
        display = _ACCOUNT_TYPE_DISPLAY.get(account_type)
        if display:
            parts.append(display)
        if last4:
            parts.append(f"...{last4}")
        suggested_name = " ".join(parts) if parts else "New Account"

        return AccountMeta(
            institution=institution,
            account_type=account_type,
            suggested_name=suggested_name,
        )
    except Exception:
        return None
