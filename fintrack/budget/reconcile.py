"""Associate individual transactions with budget entries, and the insights that
link unlocks (issue #53).

The link itself is a nullable ``budget_entry_ref`` on the corrections overlay
(see ``fintrack.ledger.repository.corrections``); this module is the workflow
layer on top of it:

- ``link_transaction`` / ``unlink_transaction`` — set/clear a link, validating
  that the transaction and the budget entry share a snapshot (the schema can't
  enforce this — corrections carry no snapshot_id).
- ``suggest_links`` — a purely local heuristic matcher (no API, so the
  classifier privacy constraint is untouched) that proposes entry candidates
  for still-unlinked transactions, always for user confirmation.
- ``budget_actuals`` — per-entry budget-vs-actual for a month: expected vs
  realized, price drift, and missed / upcoming recurring charges.

Cardinality: one transaction realizes at most one budget entry (enforced by the
one-row-per-transaction overlay); one entry is realized by many transactions
over time. The occurrence a transaction belongs to is derived from its date,
not stored.
"""

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, select

from fintrack.budget.recurrence import budget_entry_in_month, money
from fintrack.budget.repository import get_budget_entries
from fintrack.core.models import holdings, transactions
from fintrack.ledger.repository.corrections import set_budget_link
from fintrack.ledger.repository.transactions import get_transactions

_ZERO = Decimal(0)
# Actual-vs-expected classification is deliberately tight: the whole point of a
# per-entry (not per-category) link is to catch small drifts a category rollup
# hides, so anything past a cent counts as over/under.
_MATCH_TOLERANCE = Decimal("0.01")


class SnapshotMismatch(ValueError):
    """A transaction and budget entry that don't share a snapshot."""


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------


def _txn_snapshot_id(conn: Connection, transaction_id: int) -> int | None:
    """Snapshot a transaction belongs to (via its account/holding)."""
    return conn.execute(
        select(holdings.c.snapshot_id)
        .select_from(
            transactions.join(holdings, transactions.c.account_id == holdings.c.id)
        )
        .where(transactions.c.id == transaction_id)
    ).scalar()


def _entry_db_ids(conn: Connection, snapshot_id: int) -> set[int]:
    return {e["_db_id"] for e in get_budget_entries(conn, snapshot_id)}


def link_transaction(
    conn: Connection,
    snapshot_id: int,
    transaction_id: int,
    budget_entry_ref: int,
) -> None:
    """Link a transaction to a budget entry, both within ``snapshot_id``.

    Raises SnapshotMismatch if the transaction's account or the budget entry is
    not in ``snapshot_id`` — the schema can't make a cross-snapshot link
    unrepresentable here, so the repository is the guard.
    """
    txn_snap = _txn_snapshot_id(conn, transaction_id)
    if txn_snap is None:
        raise ValueError(f"Transaction {transaction_id} not found")
    if txn_snap != snapshot_id:
        raise SnapshotMismatch(
            f"Transaction {transaction_id} is in snapshot {txn_snap}, not {snapshot_id}"
        )
    if budget_entry_ref not in _entry_db_ids(conn, snapshot_id):
        raise SnapshotMismatch(
            f"Budget entry {budget_entry_ref} is not in snapshot {snapshot_id}"
        )
    set_budget_link(conn, transaction_id, budget_entry_ref)


def unlink_transaction(conn: Connection, transaction_id: int) -> None:
    """Clear a transaction's budget link (prunes an otherwise-empty overlay)."""
    set_budget_link(conn, transaction_id, None)


# ---------------------------------------------------------------------------
# Occurrence helpers
# ---------------------------------------------------------------------------


def occurs_in_month(entry: dict[str, Any], year: int, month: int) -> bool:
    """Whether the entry has at least one scheduled occurrence in the month."""
    return budget_entry_in_month(entry, year, month) != _ZERO


def expected_occurrences(entry: dict[str, Any], year: int, month: int) -> int:
    """How many occurrences of the entry are scheduled in the month."""
    if not occurs_in_month(entry, year, month):
        return 0
    return 2 if entry.get("recurrence") == "biweekly" else 1


def expected_day(entry: dict[str, Any], year: int, month: int) -> int | None:
    """The day-of-month an entry is expected to post, clamped to the month, or
    None when the entry has no single scheduled day (e.g. biweekly, or a
    continuous monthly entry)."""
    rec = entry.get("recurrence")
    if rec == "one_time":
        d = entry.get("date")
        try:
            return date.fromisoformat(d).day if d else None
        except (TypeError, ValueError):
            return None
    if rec == "biweekly" or (rec == "monthly" and entry.get("continuous")):
        return None
    day = entry.get("dayOfMonth")
    if day is None and rec == "annual":
        day = entry.get("dayOfYear")
    if day is None:
        return None
    return min(int(day), monthrange(year, month)[1])


# ---------------------------------------------------------------------------
# Suggestions (local heuristic matcher)
# ---------------------------------------------------------------------------

# Score weights (sum to 1.0). Amount and category are the strongest signals; a
# matching flow account and a near due-date add confidence.
_W_AMOUNT = Decimal("0.40")
_W_CATEGORY = Decimal("0.30")
_W_ACCOUNT = Decimal("0.20")
_W_DATE = Decimal("0.10")

# A transaction more than 25% off an entry's amount is not that entry, even if
# category and account agree — this gates candidacy so a $5 coffee never
# matches $2000 rent. Within 25% it still scores (and flags drift).
_AMOUNT_GATE = Decimal("0.25")
_DRIFT_THRESHOLD = Decimal("0.005")
_SUGGESTION_FLOOR = 0.35


@dataclass
class Suggestion:
    transaction: dict[str, Any]
    entry: dict[str, Any]
    entry_ref: int
    score: float
    reasons: list[str] = field(default_factory=list)
    drift_amount: Decimal = _ZERO  # signed: actual magnitude - budgeted

    @property
    def confidence(self) -> str:
        if self.score >= 0.8:
            return "high"
        if self.score >= 0.55:
            return "medium"
        return "low"


def _amount_component(txn_abs: Decimal, entry_amount: Decimal) -> Decimal:
    if entry_amount <= _ZERO:
        return _ZERO
    reldiff = abs(txn_abs - entry_amount) / entry_amount
    if reldiff > _AMOUNT_GATE:
        return _ZERO
    return Decimal(1) - reldiff / _AMOUNT_GATE


def _kind_matches_sign(entry_kind: str, amount: Decimal) -> bool:
    if entry_kind == "income":
        return amount > _ZERO
    return amount < _ZERO  # expense


def _score_pair(txn: dict[str, Any], entry: dict[str, Any]) -> Suggestion | None:
    amount = money(txn["amount"])
    kind = entry.get("kind", "expense")
    if not _kind_matches_sign(kind, amount):
        return None
    txn_date = txn["date"]
    year, month = txn_date.year, txn_date.month
    if not occurs_in_month(entry, year, month):
        return None

    entry_amount = money(entry.get("amount"))
    txn_abs = abs(amount)
    amount_c = _amount_component(txn_abs, entry_amount)
    if amount_c <= _ZERO:
        return None  # amount gate

    reasons: list[str] = []
    if txn_abs == entry_amount:
        reasons.append("amount exact")
    else:
        reasons.append("amount close")

    category_c = _ZERO
    if entry.get("category") and txn.get("category") == entry.get("category"):
        category_c = Decimal(1)
        reasons.append("category match")

    account_c = _ZERO
    ref = entry.get("autoAccountRef")
    if ref is not None and ref == txn.get("account_id"):
        account_c = Decimal(1)
        reasons.append("account match")

    date_c = Decimal("0.5")  # neutral when no single scheduled day
    day = expected_day(entry, year, month)
    if day is not None:
        dist = abs(txn_date.day - day)
        date_c = max(_ZERO, Decimal(1) - Decimal(dist) / Decimal(10))
        if dist <= 3:
            reasons.append("near due date")

    score = (
        _W_AMOUNT * amount_c
        + _W_CATEGORY * category_c
        + _W_ACCOUNT * account_c
        + _W_DATE * date_c
    )
    drift = txn_abs - entry_amount
    if abs(drift) > _DRIFT_THRESHOLD:
        reasons.append("price drift")
    return Suggestion(
        transaction=txn,
        entry=entry,
        entry_ref=entry["_db_id"],
        score=float(round(score, 4)),
        reasons=reasons,
        drift_amount=drift,
    )


def suggest_links(
    conn: Connection,
    snapshot_id: int,
    *,
    year: int | None = None,
    month: int | None = None,
    min_score: float = _SUGGESTION_FLOOR,
) -> list[Suggestion]:
    """Rank budget-entry candidates for each still-unlinked transaction.

    Suggestions are for confirmation only — nothing is written. Scoped to a
    month when both ``year`` and ``month`` are given, else all transactions.
    """
    entries = get_budget_entries(conn, snapshot_id)
    if not entries:
        return []
    txns = get_transactions(conn, year=year, month=month, snapshot_id=snapshot_id)
    suggestions: list[Suggestion] = []
    for txn in txns:
        if txn.get("budget_entry_ref") is not None:
            continue
        best: Suggestion | None = None
        for entry in entries:
            cand = _score_pair(txn, entry)
            if cand is None:
                continue
            if best is None or cand.score > best.score:
                best = cand
        if best is not None and best.score >= min_score:
            suggestions.append(best)
    suggestions.sort(key=lambda s: s.score, reverse=True)
    return suggestions


# ---------------------------------------------------------------------------
# Budget-vs-actual (per entry) + missed / upcoming detection
# ---------------------------------------------------------------------------


@dataclass
class EntryActual:
    entry: dict[str, Any]
    entry_ref: int
    expected: Decimal  # scheduled monthly amount (0 if inactive this month)
    actual: Decimal  # realized magnitude from linked transactions this month
    delta: Decimal  # actual - expected (positive = over-budget spend/income)
    count: int  # linked transactions this month
    status: str  # matched | over | under | missing | upcoming | inactive
    last_linked: date | None  # most recent linked transaction date, any month
    drift_amount: Decimal  # per-occurrence charge vs budgeted, when comparable


def _classify(
    entry: dict[str, Any],
    expected: Decimal,
    actual: Decimal,
    count: int,
    *,
    year: int,
    month: int,
    today: date,
) -> str:
    if expected == _ZERO:
        return "inactive"
    if count == 0:
        day = expected_day(entry, year, month)
        month_past = (year, month) < (today.year, today.month)
        is_current = (year, month) == (today.year, today.month)
        due_passed = is_current and day is not None and today.day >= day
        if month_past or due_passed:
            return "missing"
        return "upcoming"
    delta = actual - expected
    if delta > _MATCH_TOLERANCE:
        return "over"
    if delta < -_MATCH_TOLERANCE:
        return "under"
    return "matched"


def budget_actuals(
    conn: Connection,
    snapshot_id: int,
    *,
    year: int,
    month: int,
    today: date | None = None,
) -> list[EntryActual]:
    """Per-entry budget-vs-actual for ``(year, month)``.

    Sums the transactions linked to each entry within the month, compares
    against the entry's scheduled amount, and classifies the result (matched /
    over / under / missing / upcoming / inactive) so the caller can flag missed
    recurring charges and price drift.
    """
    today = today or datetime.now().astimezone().date()
    entries = get_budget_entries(conn, snapshot_id)
    month_txns = get_transactions(conn, year=year, month=month, snapshot_id=snapshot_id)
    all_txns = get_transactions(conn, snapshot_id=snapshot_id)

    by_entry_month: dict[int, list[dict]] = {}
    for t in month_txns:
        ref = t.get("budget_entry_ref")
        if ref is not None:
            by_entry_month.setdefault(ref, []).append(t)
    last_linked: dict[int, date] = {}
    for t in all_txns:
        ref = t.get("budget_entry_ref")
        if ref is not None:
            d = t["date"]
            if ref not in last_linked or d > last_linked[ref]:
                last_linked[ref] = d

    results: list[EntryActual] = []
    for entry in entries:
        ref = entry["_db_id"]
        expected = budget_entry_in_month(entry, year, month)
        linked = by_entry_month.get(ref, [])
        count = len(linked)
        net = sum((money(t["amount"]) for t in linked), _ZERO)
        actual = abs(net)
        delta = actual - expected
        status = _classify(
            entry, expected, actual, count, year=year, month=month, today=today
        )
        # Drift is only meaningful when the realized occurrence count matches
        # what was scheduled (else "delta" is just partial/extra activity).
        drift = _ZERO
        if count and count == expected_occurrences(entry, year, month):
            per_occurrence = actual / Decimal(count)
            drift = per_occurrence - money(entry.get("amount"))
        results.append(
            EntryActual(
                entry=entry,
                entry_ref=ref,
                expected=expected,
                actual=actual,
                delta=delta,
                count=count,
                status=status,
                last_linked=last_linked.get(ref),
                drift_amount=drift,
            )
        )
    return results
