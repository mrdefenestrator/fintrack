import hashlib
from collections import Counter

from spending.types import ParsedTransaction


def _base_key(txn: ParsedTransaction, account_id: int) -> str:
    return f"{txn['date'].isoformat()}|{txn['amount']}|{txn['raw_description']}|{account_id}"


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
) -> tuple[list[ParsedTransaction], list[str], list[ParsedTransaction]]:
    """Returns (new_transactions, new_fingerprints, flagged_transactions).

    - Exact fingerprint match with existing: skipped (auto-dedup).
    - Sequence > 0 whose seq-0 sibling is in existing: flagged as ambiguous.
    - Everything else: new.
    """
    new_txns: list[ParsedTransaction] = []
    new_fps: list[str] = []
    flagged: list[ParsedTransaction] = []

    for txn, fp in zip(txns, fingerprints):
        if fp in existing_fingerprints:
            continue

        key = _base_key(txn, account_id)
        seq0_fp = _fingerprint(key, 0)

        if seq0_fp in existing_fingerprints:
            flagged.append(txn)
        else:
            new_txns.append(txn)
            new_fps.append(fp)

    return new_txns, new_fps, flagged
