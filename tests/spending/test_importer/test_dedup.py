from datetime import date
from decimal import Decimal

from spending.importer.dedup import compute_fingerprints, deduplicate
from spending.types import ParsedTransaction


def test_compute_fingerprints_unique():
    txns = [
        ParsedTransaction(
            date=date(2024, 1, 15),
            amount=Decimal("-42.50"),
            raw_description="WHOLE FOODS",
        ),
        ParsedTransaction(
            date=date(2024, 1, 16),
            amount=Decimal("-5.00"),
            raw_description="COFFEE SHOP",
        ),
    ]
    fingerprints = compute_fingerprints(txns, account_id=1)
    assert len(fingerprints) == 2
    assert fingerprints[0] != fingerprints[1]


def test_compute_fingerprints_duplicate_same_day():
    """Two identical transactions get different fingerprints via sequence number."""
    txns = [
        ParsedTransaction(
            date=date(2024, 1, 16),
            amount=Decimal("-5.00"),
            raw_description="COFFEE SHOP",
        ),
        ParsedTransaction(
            date=date(2024, 1, 16),
            amount=Decimal("-5.00"),
            raw_description="COFFEE SHOP",
        ),
    ]
    fingerprints = compute_fingerprints(txns, account_id=1)
    assert fingerprints[0] != fingerprints[1]


def test_compute_fingerprints_deterministic():
    txns = [
        ParsedTransaction(
            date=date(2024, 1, 15),
            amount=Decimal("-42.50"),
            raw_description="WHOLE FOODS",
        ),
    ]
    fp1 = compute_fingerprints(txns, account_id=1)
    fp2 = compute_fingerprints(txns, account_id=1)
    assert fp1 == fp2


def test_deduplicate_removes_exact_matches():
    txns = [
        ParsedTransaction(
            date=date(2024, 1, 15),
            amount=Decimal("-42.50"),
            raw_description="WHOLE FOODS",
        ),
        ParsedTransaction(
            date=date(2024, 1, 16),
            amount=Decimal("-5.00"),
            raw_description="COFFEE SHOP",
        ),
    ]
    fingerprints = compute_fingerprints(txns, account_id=1)
    existing_fps = {fingerprints[0]}

    new_txns, new_fps, flagged = deduplicate(
        txns, fingerprints, existing_fps, account_id=1
    )
    assert len(new_txns) == 1
    assert new_txns[0]["raw_description"] == "COFFEE SHOP"
    assert len(flagged) == 0


def test_deduplicate_flags_ambiguous():
    """When existing has 1 copy but import has 2 identical, flag the second."""
    txns = [
        ParsedTransaction(
            date=date(2024, 1, 16),
            amount=Decimal("-5.00"),
            raw_description="COFFEE SHOP",
        ),
        ParsedTransaction(
            date=date(2024, 1, 16),
            amount=Decimal("-5.00"),
            raw_description="COFFEE SHOP",
        ),
    ]
    fingerprints = compute_fingerprints(txns, account_id=1)
    # Existing DB has sequence 0 but not sequence 1
    existing_fps = {fingerprints[0]}

    new_txns, new_fps, flagged = deduplicate(
        txns, fingerprints, existing_fps, account_id=1
    )
    assert len(new_txns) == 0
    assert len(flagged) == 1
    assert flagged[0]["raw_description"] == "COFFEE SHOP"
