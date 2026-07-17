"""Tests for fintrack.core.formatting.fmt_account_label — the single helper
building account-picker dropdown labels (issue 4): '{Institution} [{Type}]
{Name}', degrading to '[{Type}] {Name}' without an institution.
"""

from fintrack.core.formatting import fmt_account_label


def test_full_label_with_institution_and_type():
    account = {
        "institution": "Chase",
        "account_type": "credit_card",
        "name": "Amazon Visa",
    }
    assert fmt_account_label(account) == "Chase [Credit Card] Amazon Visa"


def test_degrades_without_institution():
    account = {"institution": None, "account_type": "checking", "name": "Main"}
    assert fmt_account_label(account) == "[Checking] Main"


def test_degrades_with_empty_string_institution():
    account = {"institution": "", "account_type": "wallet", "name": "Venmo"}
    assert fmt_account_label(account) == "[Wallet] Venmo"


def test_accepts_unified_type_key():
    """The net-worth repository uses 'type' instead of 'account_type'."""
    account = {"institution": "Chase", "type": "savings", "name": "Rainy Day"}
    assert fmt_account_label(account) == "Chase [Savings] Rainy Day"


def test_no_type_falls_back_to_institution_and_name():
    account = {"institution": "Chase", "name": "Mystery"}
    assert fmt_account_label(account) == "Chase Mystery"


def test_no_institution_or_type_is_just_name():
    account = {"name": "Mystery"}
    assert fmt_account_label(account) == "Mystery"
