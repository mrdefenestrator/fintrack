from datetime import date
from decimal import Decimal

from fintrack.core.types import ParsedTransaction
from fintrack.ledger.importer.dedup import compute_fingerprints, deduplicate


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


def test_fingerprint_canonicalizes_amount_scale():
    """Decimal('12.5') and Decimal('12.50') are the same money, so the same fp.

    Amounts round-trip through a Numeric(12,2) column as two decimals, while
    parsers can build Decimal('12.5') from an OFX float; canonicalizing keeps
    import-time and stored fingerprints in lockstep.
    """
    short = [
        ParsedTransaction(
            date=date(2024, 1, 15),
            amount=Decimal("12.5"),
            raw_description="WHOLE FOODS",
        )
    ]
    padded = [
        ParsedTransaction(
            date=date(2024, 1, 15),
            amount=Decimal("12.50"),
            raw_description="WHOLE FOODS",
        )
    ]
    assert compute_fingerprints(short, account_id=1) == compute_fingerprints(
        padded, account_id=1
    )


def test_fingerprint_embeds_account_id():
    """The same transaction under two accounts fingerprints differently.

    This is the invariant the holdings-split migration violated: remapping
    account_id without recomputing fingerprints broke dedup.
    """
    txns = [
        ParsedTransaction(
            date=date(2024, 1, 15),
            amount=Decimal("-42.50"),
            raw_description="WHOLE FOODS",
        )
    ]
    assert compute_fingerprints(txns, account_id=1) != compute_fingerprints(
        txns, account_id=2
    )


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

    new_txns, _new_fps, flagged, flagged_fps = deduplicate(
        txns, fingerprints, existing_fps, account_id=1
    )
    assert len(new_txns) == 1
    assert new_txns[0]["raw_description"] == "COFFEE SHOP"
    assert len(flagged) == 0
    assert flagged_fps == []


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

    new_txns, _new_fps, flagged, flagged_fps = deduplicate(
        txns, fingerprints, existing_fps, account_id=1
    )
    assert len(new_txns) == 0
    assert len(flagged) == 1
    assert flagged[0]["raw_description"] == "COFFEE SHOP"
    # Flagged rows keep their whole-file fingerprint (sequence 1 here).
    assert flagged_fps == [fingerprints[1]]
