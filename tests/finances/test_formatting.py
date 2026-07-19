"""Tests for fintrack.core.formatting.fmt_account_label — the single helper
building account-picker dropdown labels: '{Institution} {Name}', degrading to
just '{Name}' without an institution. The account type is intentionally
omitted (it read as clutter in the pickers / filter chips).
"""

from fintrack.core.formatting import fmt_account_label


def test_full_label_with_institution():
    account = {
        "institution": "Chase",
        "account_type": "credit_card",
        "name": "Amazon Visa",
    }
    assert fmt_account_label(account) == "Chase Amazon Visa"


def test_degrades_without_institution():
    account = {"institution": None, "account_type": "checking", "name": "Main"}
    assert fmt_account_label(account) == "Main"


def test_degrades_with_empty_string_institution():
    account = {"institution": "", "account_type": "wallet", "name": "Venmo"}
    assert fmt_account_label(account) == "Venmo"


def test_type_is_ignored():
    """The type is no longer part of the label, regardless of key name."""
    account = {"institution": "Chase", "type": "savings", "name": "Rainy Day"}
    assert fmt_account_label(account) == "Chase Rainy Day"


def test_no_institution_is_just_name():
    account = {"name": "Mystery"}
    assert fmt_account_label(account) == "Mystery"
