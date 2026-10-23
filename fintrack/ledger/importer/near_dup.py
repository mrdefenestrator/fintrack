"""Near-duplicate matching (issue #82).

Exact dedup (bank transaction id, then fingerprint) misses re-exports whose
descriptions changed. This pairs each incoming row with at most one existing
row that looks like the same money movement, so the user can review it. A
match is only ever a flag — nothing here skips or hides a row.

Pure and local: it sees only rows already on this machine and never calls out
(the classifier privacy constraint holds — transaction data is never sent to
the Claude API).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from typing import TypedDict

# The agreed criteria; tune here.
DATE_WINDOW_DAYS = 3
DESCRIPTION_SIMILARITY = 0.6

_CENTS = Decimal("0.01")


class NearDupRow(TypedDict):
    date: date
    amount: Decimal
    raw_description: str
    normalized_merchant: str


@dataclass(frozen=True)
class NearDupMatch:
    incoming: int  # index into the incoming list
    existing: int  # index into the existing list
    day_gap: int
    similarity: float  # 1.0 when the normalized merchants are identical
    reason: str


def description_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.casefold(), b.casefold()).ratio()


def merchant_similarity(a: NearDupRow, b: NearDupRow) -> float:
    """The better of raw-description and normalized-name similarity.

    Raw text alone scores a bank string against a cleaned-up name poorly
    ("TRADER JOE S #123 OAKLAND CA" vs "Trader Joe's" is 0.55); normalization
    strips the store numbers and processor prefixes first (0.92 there).
    """
    return max(
        description_similarity(a["raw_description"], b["raw_description"]),
        description_similarity(a["normalized_merchant"], b["normalized_merchant"]),
    )


def _reason(day_gap: int, similarity: float, same_merchant: bool) -> str:
    gap = (
        "same day"
        if day_gap == 0
        else f"{day_gap} day{'s' if day_gap > 1 else ''} apart"
    )
    merchant = (
        "same merchant" if same_merchant else f"merchant {similarity:.0%} similar"
    )
    return f"same amount · {gap} · {merchant}"


def _cents(amount: Decimal) -> Decimal:
    return Decimal(amount).quantize(_CENTS)


def find_near_duplicates(
    incoming: list[NearDupRow], existing: list[NearDupRow]
) -> list[NearDupMatch]:
    """Pair incoming rows with existing rows that are likely the same charge.

    A pair qualifies when the amounts match to the cent, the dates are within
    DATE_WINDOW_DAYS, and the merchants are similar: identical normalized
    names, or a merchant_similarity() of at least DESCRIPTION_SIMILARITY. The
    caller supplies only rows from the same account and from other imports.

    Pairing is one-to-one, so two genuine identical charges in both files
    don't collapse onto one existing row: the closest date wins, then the
    higher similarity, then input order (stable for tests).
    """
    by_amount: dict[Decimal, list[int]] = {}
    for j, old in enumerate(existing):
        by_amount.setdefault(_cents(old["amount"]), []).append(j)

    candidates: list[tuple[int, float, int, int, bool]] = []
    for i, new in enumerate(incoming):
        for j in by_amount.get(_cents(new["amount"]), []):
            old = existing[j]
            day_gap = abs((new["date"] - old["date"]).days)
            if day_gap > DATE_WINDOW_DAYS:
                continue
            same_merchant = new["normalized_merchant"] == old["normalized_merchant"]
            similarity = 1.0 if same_merchant else merchant_similarity(new, old)
            if similarity < DESCRIPTION_SIMILARITY:
                continue
            candidates.append((day_gap, -similarity, i, j, same_merchant))

    candidates.sort()
    matches: list[NearDupMatch] = []
    used_in: set[int] = set()
    used_ex: set[int] = set()
    for day_gap, neg_sim, i, j, same_merchant in candidates:
        if i in used_in or j in used_ex:
            continue
        used_in.add(i)
        used_ex.add(j)
        matches.append(
            NearDupMatch(
                incoming=i,
                existing=j,
                day_gap=day_gap,
                similarity=-neg_sim,
                reason=_reason(day_gap, -neg_sim, same_merchant),
            )
        )
    matches.sort(key=lambda m: m.incoming)
    return matches
