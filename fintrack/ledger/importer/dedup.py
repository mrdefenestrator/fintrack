import hashlib
from collections import Counter
from decimal import Decimal

from fintrack.core.types import ParsedTransaction

_CENTS = Decimal("0.01")


def canonical_amount(amount: Decimal) -> str:
    """Canonical 2-decimal string for an amount, e.g. Decimal('12.5') -> '12.50'.

    The fingerprint must be reproducible from the value the DB stores in a
    Numeric(12,2) column, which always round-trips to two decimals. Parsers
    build amounts straight from statement text (an OFX float can yield
    Decimal('12.5')), so canonicalizing here keeps import-time fingerprints,
    a fresh DB's stored fingerprints, and the recompute migration all in
    lockstep. See migration recompute_transaction_fingerprints.
    """
    return str(Decimal(amount).quantize(_CENTS))


def _base_key(txn: ParsedTransaction, account_id: int) -> str:
    amount = canonical_amount(txn["amount"])
    return f"{txn['date'].isoformat()}|{amount}|{txn['raw_description']}|{account_id}"


def _fingerprint(base_key: str, seq: int) -> str:
    return hashlib.sha256(f"{base_key}|{seq}".encode()).hexdigest()


def compute_fingerprints(txns: list[ParsedTransaction], account_id: int) -> list[str]:
    counts: Counter[str] = Counter()
    fingerprints: list[str] = []

    for txn in txns:
        key = _base_key(txn, account_id)
        seq = counts[key]
        counts[key] += 1
        fingerprints.append(_fingerprint(key, seq))

    return fingerprints


def deduplicate(
    txns: list[ParsedTransaction],
    fingerprints: list[str],
    existing_fingerprints: set[str],
    account_id: int,
) -> tuple[list[ParsedTransaction], list[str], list[ParsedTransaction], list[str]]:
    """Returns (new_txns, new_fps, flagged_txns, flagged_fps).

    - Exact fingerprint match with existing: skipped (auto-dedup).
    - Sequence > 0 whose seq-0 sibling is in existing: flagged as ambiguous.
    - Everything else: new.

    Flagged transactions carry the same fingerprints computed over the whole
    file (their entries from `fingerprints`); they must not be re-fingerprinted
    with a fresh sequence counter, or their stored fingerprint would no longer
    match the whole-file sequence a future import compares against.
    """
    new_txns: list[ParsedTransaction] = []
    new_fps: list[str] = []
    flagged: list[ParsedTransaction] = []
    flagged_fps: list[str] = []

    for txn, fp in zip(txns, fingerprints):
        if fp in existing_fingerprints:
            continue

        key = _base_key(txn, account_id)
        seq0_fp = _fingerprint(key, 0)

        if seq0_fp in existing_fingerprints:
            flagged.append(txn)
            flagged_fps.append(fp)
        else:
            new_txns.append(txn)
            new_fps.append(fp)

    return new_txns, new_fps, flagged, flagged_fps
