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
- ``dismiss_suggestion`` / ``undismiss_suggestions`` — remember (or forget)
  rejected suggestions so they aren't proposed again.
- ``budget_actuals`` — per-entry budget-vs-actual for a month: expected vs
  realized, price drift, and missed / upcoming recurring charges.

Cardinality: one transaction realizes at most one budget entry (enforced by the
one-row-per-transaction overlay); one entry is realized by many transactions
over time. The occurrence a transaction belongs to is derived from its date,
not stored.
"""

from calendar import monthrange
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, select

from fintrack.budget.recurrence import budget_entry_in_month, money
from fintrack.budget.repository import get_budget_entries, get_budget_entry
from fintrack.core.models import holdings, transactions
from fintrack.ledger.repository.aggregations import get_monthly_category_totals
from fintrack.ledger.repository.corrections import (
    add_link_dismissal,
    clear_link_dismissals,
    get_link_dismissals,
    set_budget_link,
)
from fintrack.ledger.repository.transactions import (
    get_budget_link_date_ranges,
    get_transactions,
)

_ZERO = Decimal(0)
# How far an amount may differ from its budgeted value and still count as equal,
# for both over/under classification and suggester price-drift flags. It is
# deliberately tight: the whole point of a per-entry (not per-category) link is
# to catch small drifts a category rollup hides, so anything past a cent counts.
# A cent (not half a cent) also absorbs sub-cent noise in prorated expecteds.
_TOLERANCE = Decimal("0.01")


class SnapshotMismatch(ValueError):
    """A transaction and budget entry that don't share a snapshot."""


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------


def _txn_snapshot_and_amount(
    conn: Connection, transaction_id: int
) -> tuple[int, Decimal] | None:
    """(snapshot id, amount) for a transaction, via its account/holding."""
    row = conn.execute(
        select(holdings.c.snapshot_id, transactions.c.amount)
        .select_from(
            transactions.join(holdings, transactions.c.account_id == holdings.c.id)
        )
        .where(transactions.c.id == transaction_id)
    ).first()
    return (row[0], money(row[1])) if row else None


def _validated_pair(
    conn: Connection, snapshot_id: int, transaction_id: int, budget_entry_ref: int
) -> tuple[Decimal, dict[str, Any]]:
    """Return the transaction's amount and the entry, after checking both are
    in ``snapshot_id`` (ValueError / SnapshotMismatch otherwise)."""
    found = _txn_snapshot_and_amount(conn, transaction_id)
    if found is None:
        raise ValueError(f"Transaction {transaction_id} not found")
    txn_snap, amount = found
    if txn_snap != snapshot_id:
        raise SnapshotMismatch(
            f"Transaction {transaction_id} is in snapshot {txn_snap}, not {snapshot_id}"
        )
    entry = get_budget_entry(conn, snapshot_id, budget_entry_ref)
    if entry is None:
        raise SnapshotMismatch(
            f"Budget entry {budget_entry_ref} is not in snapshot {snapshot_id}"
        )
    return amount, entry


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

    Either sign may link: a transaction against its entry's kind (a refund on
    an expense, a clawback on income) is a *reversal* that nets against the
    entry's actual (see ``is_reversal``).
    """
    _validated_pair(conn, snapshot_id, transaction_id, budget_entry_ref)
    set_budget_link(conn, transaction_id, budget_entry_ref)


def unlink_transaction(conn: Connection, transaction_id: int) -> None:
    """Clear a transaction's budget link (prunes an otherwise-empty overlay)."""
    set_budget_link(conn, transaction_id, None)


# ---------------------------------------------------------------------------
# Occurrence helpers
# ---------------------------------------------------------------------------


def dismiss_suggestion(
    conn: Connection,
    snapshot_id: int,
    transaction_id: int,
    budget_entry_ref: int,
) -> None:
    """Stop suggesting ``budget_entry_ref`` for ``transaction_id`` (issue #70).

    Same snapshot guard as ``link_transaction``. The transaction stays
    linkable to that entry by hand, and may still be suggested other entries.
    """
    _validated_pair(conn, snapshot_id, transaction_id, budget_entry_ref)
    add_link_dismissal(conn, transaction_id, budget_entry_ref)


def undismiss_suggestions(conn: Connection, transaction_id: int) -> int:
    """Forget every dismissed suggestion for a transaction; returns the count."""
    return clear_link_dismissals(conn, transaction_id)


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


def _measured_by_category(entry: dict[str, Any], claims: Counter) -> bool:
    """A variable, continuous entry that alone claims its category is measured
    by that category's month total, not by links (see ``budget_actuals``).
    ``claims`` counts entries per category across the snapshot."""
    category = entry.get("category")
    return bool(entry.get("continuous") and category and claims[category] == 1)


def _kind_matches_sign(entry_kind: str, amount: Decimal) -> bool:
    if entry_kind == "income":
        return amount > _ZERO
    return amount < _ZERO  # expense


def is_reversal(entry_kind: str | None, amount: Any) -> bool:
    """True for a linked transaction that runs against its entry's kind — a
    refund (deposit) on an expense, a clawback (charge) on income. Reversals
    net against the entry's actual but never count as the money having moved:
    they don't realize an occurrence, satisfy a missed-charge check, feed price
    drift, or get suggested."""
    amount = money(amount)
    if entry_kind == "income":
        return amount < _ZERO
    return amount > _ZERO  # expense


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
    if abs(drift) > _TOLERANCE:
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
    Pairs the user dismissed are skipped, so a transaction whose best match
    was rejected may be offered its next-best entry instead. Entries measured
    by category total are never proposed: their category already counts the
    transaction, and a link would only re-pin its category (silently
    recategorizing it when the guess is wrong).
    """
    entries = get_budget_entries(conn, snapshot_id)
    claims = Counter(e["category"] for e in entries if e.get("category"))
    entries = [e for e in entries if not _measured_by_category(e, claims)]
    if not entries:
        return []
    txns = get_transactions(conn, year=year, month=month, snapshot_id=snapshot_id)
    dismissed = get_link_dismissals(conn, snapshot_id)
    suggestions: list[Suggestion] = []
    for txn in txns:
        if txn.get("budget_entry_ref") is not None:
            continue
        best: Suggestion | None = None
        for entry in entries:
            if (txn["id"], entry["_db_id"]) in dismissed:
                continue
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
    # Realized magnitude this month (see ``source``), net of reversals — may
    # go negative when refunds exceed charges.
    actual: Decimal
    delta: Decimal  # actual - expected
    count: int  # transactions behind ``actual``, excluding reversals
    # matched | over | under | missing | upcoming | unlinked | inactive
    status: str
    last_linked: date | None  # most recent linked transaction date, any month
    drift_amount: Decimal  # per-occurrence charge vs budgeted, when comparable
    # "links": actual is the sum of transactions linked to this entry.
    # "category": a variable (continuous) line that is the only entry claiming
    # its category — actual is that category's total for the month, since
    # nobody links every coffee to "Dining Out".
    source: str = "links"
    # Magnitude of linked reversals (refunds / clawbacks) netted into
    # ``actual``; always 0 for category-measured lines.
    reversed_amount: Decimal = _ZERO

    @property
    def tone(self) -> str:
        """good | bad | neutral — whether the status is favorable. Over/under
        flip with kind: spending over budget is bad, earning over it is good."""
        if self.status == "matched":
            return "good"
        if self.status == "missing":
            return "bad"
        if self.status in ("over", "under"):
            income = self.entry.get("kind") == "income"
            return "good" if (self.status == "over") == income else "bad"
        return "neutral"


def _classify_delta(delta: Decimal) -> str:
    if delta > _TOLERANCE:
        return "over"
    if delta < -_TOLERANCE:
        return "under"
    return "matched"


def _classify_linked(
    entry: dict[str, Any],
    expected: Decimal,
    actual: Decimal,
    count: int,
    *,
    tracked: bool,
    year: int,
    month: int,
    today: date,
) -> str:
    """Status for an entry measured by its linked transactions.

    With nothing linked this month, the entry is only "missing" (or
    "upcoming") if it has been linked in an earlier month — i.e. it is a
    recurring charge you track that didn't show up. An entry that has never
    been linked is "unlinked": absent links say nothing about whether the money
    moved, so flagging it missing would just be noise.
    """
    if expected == _ZERO:
        return "inactive"
    if count == 0:
        if not tracked:
            return "unlinked"
        day = expected_day(entry, year, month)
        month_past = (year, month) < (today.year, today.month)
        is_current = (year, month) == (today.year, today.month)
        due_passed = is_current and day is not None and today.day >= day
        if month_past or due_passed:
            return "missing"
        return "upcoming"
    return _classify_delta(actual - expected)


def budget_actuals(
    conn: Connection,
    snapshot_id: int,
    *,
    year: int,
    month: int,
    today: date | None = None,
) -> list[EntryActual]:
    """Per-entry budget-vs-actual for ``(year, month)``.

    Discrete entries (bills, paychecks) are measured by the transactions linked
    to them. A variable, continuous entry that alone claims its category is
    measured by that category's month total instead (``source="category"``).
    Each result is classified so the caller can flag missed recurring charges
    and price drift.
    """
    today = today or datetime.now().astimezone().date()
    entries = get_budget_entries(conn, snapshot_id)
    month_txns = get_transactions(conn, year=year, month=month, snapshot_id=snapshot_id)
    link_ranges = get_budget_link_date_ranges(conn, snapshot_id)
    category_totals = {
        row["category"]: row
        for row in get_monthly_category_totals(
            conn, year=year, month=month, snapshot_id=snapshot_id
        )
    }
    claims = Counter(e["category"] for e in entries if e.get("category"))

    by_entry_month: dict[int, list[dict]] = {}
    for t in month_txns:
        ref = t.get("budget_entry_ref")
        if ref is not None:
            by_entry_month.setdefault(ref, []).append(t)
    month_start = date(year, month, 1)

    results: list[EntryActual] = []
    for entry in entries:
        ref = entry["_db_id"]
        expected = budget_entry_in_month(entry, year, month)
        category = entry.get("category")
        drift = _ZERO
        reversed_amount = _ZERO
        if _measured_by_category(entry, claims):
            source = "category"
            row = category_totals.get(category)
            count = row["count"] if row else 0
            actual = abs(money(row["total"])) if row else _ZERO
            status = (
                "inactive" if expected == _ZERO else _classify_delta(actual - expected)
            )
        else:
            source = "links"
            linked = by_entry_month.get(ref, [])
            kind = entry.get("kind", "expense")
            flows = [t for t in linked if not is_reversal(kind, t["amount"])]
            reversed_amount = abs(
                sum(
                    (
                        money(t["amount"])
                        for t in linked
                        if is_reversal(kind, t["amount"])
                    ),
                    _ZERO,
                )
            )
            count = len(flows)
            flow_total = abs(sum((money(t["amount"]) for t in flows), _ZERO))
            actual = flow_total - reversed_amount
            tracked = ref in link_ranges and link_ranges[ref][0] < month_start
            status = _classify_linked(
                entry,
                expected,
                actual,
                count,
                tracked=tracked,
                year=year,
                month=month,
                today=today,
            )
            # Drift is only meaningful when the realized occurrence count
            # matches what was scheduled (else the delta is partial/extra
            # activity, not a price change).
            if count and count == expected_occurrences(entry, year, month):
                drift = flow_total / Decimal(count) - money(entry.get("amount"))
        results.append(
            EntryActual(
                entry=entry,
                entry_ref=ref,
                expected=expected,
                actual=actual,
                delta=actual - expected,
                count=count,
                status=status,
                last_linked=link_ranges[ref][1] if ref in link_ranges else None,
                drift_amount=drift,
                source=source,
                reversed_amount=reversed_amount,
            )
        )
    return results
