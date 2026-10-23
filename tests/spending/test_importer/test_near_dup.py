from datetime import date
from decimal import Decimal

from fintrack.ledger.importer.near_dup import (
    DATE_WINDOW_DAYS,
    find_near_duplicates,
)


def _row(day, amount, desc, merchant=None):
    return {
        "date": date(2026, 10, day),
        "amount": Decimal(amount),
        "raw_description": desc,
        "normalized_merchant": merchant or desc.upper(),
    }


def test_changed_description_matches():
    [m] = find_near_duplicates(
        [_row(2, "-56.78", "Uber Trip Help.Uber.Com", "UBER TRIP")],
        [_row(1, "-56.78", "UBER *TRIP HELP.UBER.COM", "UBER")],
    )
    assert (m.incoming, m.existing, m.day_gap) == (0, 0, 1)
    assert m.reason.startswith("same amount · 1 day apart · merchant ")


def test_same_normalized_merchant_matches_regardless_of_raw_text():
    [m] = find_near_duplicates(
        [_row(1, "-5.00", "SQ *BLUE BOTTLE 123", "BLUE BOTTLE")],
        [_row(1, "-5.00", "Blue Bottle Coffee Oakland CA", "BLUE BOTTLE")],
    )
    assert m.reason == "same amount · same day · same merchant"
    assert m.similarity == 1.0


def test_amount_must_match_to_the_cent():
    assert not find_near_duplicates(
        [_row(1, "-5.01", "COFFEE")], [_row(1, "-5.00", "COFFEE")]
    )
    # Representation doesn't matter, value does.
    assert find_near_duplicates(
        [_row(1, "-5.5", "COFFEE")], [_row(1, "-5.50", "COFFEE")]
    )


def test_date_window_boundary():
    edge = 1 + DATE_WINDOW_DAYS
    assert find_near_duplicates(
        [_row(edge, "-9.99", "NETFLIX")], [_row(1, "-9.99", "NETFLIX")]
    )
    assert not find_near_duplicates(
        [_row(edge + 1, "-9.99", "NETFLIX")], [_row(1, "-9.99", "NETFLIX")]
    )


def test_dissimilar_merchant_is_not_a_match():
    assert not find_near_duplicates(
        [_row(1, "-20.00", "SHELL OIL 5744")], [_row(1, "-20.00", "TRADER JOES")]
    )


def test_pairing_is_one_to_one():
    # Two genuine identical coffees in both files: each pairs with one.
    incoming = [_row(1, "-5.00", "Coffee Shop"), _row(1, "-5.00", "Coffee Shop")]
    existing = [_row(1, "-5.00", "COFFEE SHOP #1", "COFFEE SHOP")]
    matches = find_near_duplicates(incoming, existing)
    assert [(m.incoming, m.existing) for m in matches] == [(0, 0)]


def test_closest_date_wins():
    incoming = [_row(3, "-5.00", "COFFEE"), _row(4, "-5.00", "COFFEE")]
    existing = [_row(4, "-5.00", "COFFEE")]
    [m] = find_near_duplicates(incoming, existing)
    assert (m.incoming, m.day_gap) == (1, 0)


def test_normalized_names_rescue_raw_vs_cleaned_descriptions():
    # Raw text scores 0.55; the normalized names score 0.92.
    [m] = find_near_duplicates(
        [_row(1, "-30.00", "TRADER JOE S #123 OAKLAND CA", "TRADER JOE S")],
        [_row(1, "-30.00", "Trader Joe's", "TRADER JOE'S")],
    )
    assert m.reason == "same amount · same day · merchant 92% similar"
