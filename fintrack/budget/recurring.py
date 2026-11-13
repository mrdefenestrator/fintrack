"""Recurring charges the budget doesn't cover, and recurring price changes
(issue #100).

A merchant is *recurring* when its charges (or deposits) arrive on a steady
cadence — weekly, biweekly, monthly or yearly, allowing a few days' drift — for
a similar amount each time, at least 3 times (2 for yearly). Each series is
then matched against the budget:

- ``linked`` — its transactions are already linked to a budget entry;
- ``unlinked`` — an entry looks like it (same name, or same category and
  amount) but nothing is linked yet;
- ``covered`` — a variable line measured by category total claims the
  merchant's category (see ``reconcile._measured_by_category``), so the money
  is budgeted even though no entry names it;
- ``not_budgeted`` — none of the above: the finding this module exists for.

A series whose latest charges settled at a new amount also reports a **price
change** (``previous_amount`` → ``amount`` since ``price_change_since``),
budgeted or not.

Purely local, like the link suggester: no transaction data goes to the Claude
API. Transfers are excluded; income series are reported with ``kind="income"``
so callers can show them separately.
"""

from calendar import monthrange
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from statistics import median
from typing import Any

from sqlalchemy import Connection

from fintrack.budget.reconcile import _measured_by_category, link_transaction
from fintrack.budget.recurrence import entry_active, money
from fintrack.budget.repository import (
    add_budget_entry,
    add_recurring_dismissal,
    get_budget_entries,
    get_recurring_dismissals,
    remove_recurring_dismissal,
    update_budget_entry_by_id,
)
from fintrack.ledger.repository.transactions import get_transactions

_ZERO = Decimal(0)

# How far back to look. A little over a year, so a yearly charge can show
# twice.
LOOKBACK_DAYS = 400
# Amounts within this fraction of each other count as "the same price".
AMOUNT_TOLERANCE = Decimal("0.10")
# Categories never treated as recurring spending.
EXCLUDED_CATEGORIES = frozenset({"Transfer"})

STATUSES = ("not_budgeted", "unlinked", "covered", "linked")


@dataclass(frozen=True)
class Cadence:
    key: str  # weekly | biweekly | monthly | annual
    recurrence: str  # the budget entry recurrence it maps to
    days: float  # nominal interval
    low: int  # shortest interval that still counts (drift)
    high: int  # longest interval that still counts
    min_count: int  # charges needed to call it recurring
    suffix: str  # "/mo" etc. for terse amounts


CADENCES = (
    Cadence("weekly", "weekly", 7, 5, 9, 3, "/wk"),
    Cadence("biweekly", "biweekly", 14, 11, 17, 3, "/2wk"),
    Cadence("monthly", "monthly", 30.44, 25, 36, 3, "/mo"),
    Cadence("annual", "annual", 365.25, 340, 390, 2, "/yr"),
)
CADENCE_BY_KEY = {c.key: c for c in CADENCES}


# ---------------------------------------------------------------------------
# Pattern detection (pure: one merchant's dated amounts in, a pattern out)
# ---------------------------------------------------------------------------


@dataclass
class Pattern:
    cadence: Cadence
    amount: Decimal  # typical magnitude of the current price
    count: int
    first_date: date
    last_date: date
    next_date: date
    # Set when the latest charges settled at a new price recently.
    previous_amount: Decimal | None = None
    price_change_since: date | None = None


def _median_amount(values: list[Decimal]) -> Decimal:
    return money(median(values)).quantize(Decimal("0.01"))


def _same_price(a: Decimal, b: Decimal) -> bool:
    if a == b:
        return True
    base = max(a, b)
    return base > _ZERO and abs(a - b) / base <= AMOUNT_TOLERANCE


def _price_runs(amounts: list[Decimal]) -> list[list[int]]:
    """Split a time-ordered series into runs of consecutive same-price
    charges (indices), comparing each charge to its run's first."""
    runs: list[list[int]] = []
    for i, amount in enumerate(amounts):
        if runs and _same_price(amounts[runs[-1][0]], amount):
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def _cadence_for(intervals: list[int]) -> Cadence | None:
    """The cadence nearly all intervals fit: the median's cadence, with at
    most one interval in five off it and none shorter than its drift allows
    (two charges in one period isn't a subscription)."""
    mid = median(intervals)
    for cadence in CADENCES:
        if cadence.low <= mid <= cadence.high:
            fits = sum(cadence.low <= d <= cadence.high for d in intervals)
            if min(intervals) < cadence.low:
                return None
            if fits * 5 >= len(intervals) * 4:
                return cadence
            return None
    return None


def next_occurrence(last: date, cadence: Cadence) -> date:
    """The charge after ``last`` on ``cadence`` (months clamp to month-end)."""
    if cadence.key in ("weekly", "biweekly"):
        return last + timedelta(days=int(cadence.days))
    if cadence.key == "monthly":
        year, month = (
            (last.year + 1, 1) if last.month == 12 else (last.year, last.month + 1)
        )
    else:
        year, month = last.year + 1, last.month
    return date(year, month, min(last.day, monthrange(year, month)[1]))


def detect_pattern(charges: list[tuple[date, Decimal]], today: date) -> Pattern | None:
    """Whether one merchant's charges recur, and how.

    ``charges`` are (date, magnitude) pairs, any order. Returns None for a
    series that is too short, irregular in timing, irregular in amount, or
    stale (no charge for well over one interval: likely cancelled).

    Amounts may step to a new price: the series is split into runs of
    same-price charges, and up to three runs are allowed so long as every run
    but the first and last has at least two charges (one odd charge in the
    middle is noise, not a price). The latest run is the current price.
    """
    if len(charges) < 2:
        return None
    charges = sorted(charges)
    dates = [d for d, _ in charges]
    amounts = [money(a) for _, a in charges]
    intervals = [(b - a).days for a, b in pairwise(dates)]
    cadence = _cadence_for(intervals)
    if cadence is None or len(charges) < cadence.min_count:
        return None
    if (today - dates[-1]).days > cadence.days * 1.5 + (cadence.high - cadence.days):
        return None  # stale

    runs = _price_runs(amounts)
    if len(runs) > 3 or any(len(r) < 2 for r in runs[1:-1]):
        return None
    current = runs[-1]
    pattern = Pattern(
        cadence=cadence,
        amount=_median_amount([amounts[i] for i in current]),
        count=len(charges),
        first_date=dates[0],
        last_date=dates[-1],
        next_date=next_occurrence(dates[-1], cadence),
    )
    if len(runs) > 1:
        previous = runs[-2]
        since = dates[current[0]]
        # Only a recent change is news; report it until a couple of
        # intervals (at least three months) have passed.
        recent = max(92, int(cadence.days * 2))
        if len(previous) >= (2 if cadence.min_count > 2 else 1) and (
            (today - since).days <= recent
        ):
            pattern.previous_amount = _median_amount([amounts[i] for i in previous])
            pattern.price_change_since = since
    return pattern


# ---------------------------------------------------------------------------
# Matching series against the budget
# ---------------------------------------------------------------------------


@dataclass
class RecurringCharge:
    merchant: str
    kind: str  # expense | income
    category: str | None
    pattern: Pattern
    account_id: int | None  # where it usually posts
    transaction_ids: list[int] = field(default_factory=list)
    status: str = "not_budgeted"
    entry_ref: int | None = None
    entry_description: str | None = None

    @property
    def amount(self) -> Decimal:
        """Typical current amount, signed like the budget (expenses
        negative)."""
        return -self.pattern.amount if self.kind == "expense" else self.pattern.amount

    @property
    def cadence(self) -> Cadence:
        return self.pattern.cadence

    @property
    def price_changed(self) -> bool:
        return self.pattern.price_change_since is not None

    @property
    def needs_attention(self) -> bool:
        return self.status == "not_budgeted" or self.price_changed


def _norm(name: str | None) -> str:
    return " ".join((name or "").lower().split())


def _names_match(merchant: str, description: str | None) -> bool:
    m, d = _norm(merchant), _norm(description)
    return bool(m and d) and (m in d or d in m)


def _entry_matches(charge: RecurringCharge, entry: dict[str, Any]) -> bool:
    """An entry that plausibly *is* this series though nothing links them:
    same kind, and either its description names the merchant or it shares
    the category, recurrence and price."""
    if entry.get("kind", "expense") != charge.kind:
        return False
    if _names_match(charge.merchant, entry.get("description")):
        return True
    return bool(
        charge.category
        and entry.get("category") == charge.category
        and entry.get("recurrence") == charge.cadence.recurrence
        and _same_price(money(entry.get("amount")), charge.pattern.amount)
    )


def _classify(
    charge: RecurringCharge,
    links: Counter,
    entries: list[dict[str, Any]],
    claims: Counter,
    described: dict[int, str | None] | None = None,
) -> None:
    if described is None:
        described = {e["_db_id"]: e.get("description") for e in entries}
    if links:
        ref = links.most_common(1)[0][0]
        charge.status, charge.entry_ref = "linked", ref
        charge.entry_description = described.get(ref)
        return
    for entry in entries:
        if _entry_matches(charge, entry) and not _measured_by_category(entry, claims):
            charge.status, charge.entry_ref = "unlinked", entry["_db_id"]
            charge.entry_description = entry.get("description")
            return
    for entry in entries:
        if (
            entry.get("kind", "expense") == charge.kind
            and charge.category
            and entry.get("category") == charge.category
            and _measured_by_category(entry, claims)
        ):
            charge.status, charge.entry_ref = "covered", entry["_db_id"]
            charge.entry_description = entry.get("description")
            return


def series_key(txn: dict[str, Any]) -> tuple[str, str] | None:
    """The (merchant, kind) series a transaction would belong to, or None
    for one that can't recur (no merchant, zero amount, a transfer)."""
    merchant = (txn.get("merchant") or "").strip()
    amount = money(txn["amount"])
    if not merchant or amount == _ZERO:
        return None
    if txn.get("category") in EXCLUDED_CATEGORIES:
        return None
    return merchant, "income" if amount > _ZERO else "expense"


def _scan(
    conn: Connection,
    snapshot_id: int,
    today: date,
    dismissed: set[str],
) -> tuple[list[RecurringCharge], dict[tuple[str, str], int]]:
    """Every recurring series in the window ending ``today`` (unsorted), plus
    how many charges each (merchant, kind) had in it, recurring or not."""
    start = today - timedelta(days=LOOKBACK_DAYS)
    groups: dict[tuple[str, str], list[dict]] = {}
    for txn in get_transactions(conn, snapshot_id=snapshot_id):
        if not (start <= txn["date"] <= today):
            continue
        key = series_key(txn)
        if key is None or key[0] in dismissed:
            continue
        groups.setdefault(key, []).append(txn)

    # Matched against today's plan (issue #136): an entry that ended or hasn't
    # started can't cover a series, though a series linked to one still says so.
    entries = get_budget_entries(conn, snapshot_id, as_of=today)
    described = {e["_db_id"]: e.get("description") for e in entries}
    entries = [e for e in entries if entry_active(e, today.year, today.month)]
    claims = Counter(e["category"] for e in entries if e.get("category"))
    found: list[RecurringCharge] = []
    for (merchant, kind), txns in groups.items():
        pattern = detect_pattern(
            [(t["date"], abs(money(t["amount"]))) for t in txns], today
        )
        if pattern is None:
            continue
        categories = Counter(
            t["category"]
            for t in txns
            if t.get("category") not in (None, "Uncategorized")
        )
        accounts = Counter(t["account_id"] for t in txns)
        charge = RecurringCharge(
            merchant=merchant,
            kind=kind,
            category=categories.most_common(1)[0][0] if categories else None,
            pattern=pattern,
            account_id=accounts.most_common(1)[0][0] if accounts else None,
            transaction_ids=[t["id"] for t in sorted(txns, key=lambda t: t["date"])],
        )
        links = Counter(
            t["budget_entry_ref"] for t in txns if t.get("budget_entry_ref") is not None
        )
        _classify(charge, links, entries, claims, described)
        found.append(charge)
    return found, {key: len(txns) for key, txns in groups.items()}


def find_recurring(
    conn: Connection,
    snapshot_id: int,
    *,
    today: date | None = None,
    include_dismissed: bool = False,
) -> list[RecurringCharge]:
    """Every recurring series in the snapshot's last LOOKBACK_DAYS, matched
    against the budget. Merchants the user dismissed are left out unless
    ``include_dismissed``. Sorted: needs attention first, then by size."""
    today = today or datetime.now().astimezone().date()
    dismissed = (
        set() if include_dismissed else get_recurring_dismissals(conn, snapshot_id)
    )
    found, _ = _scan(conn, snapshot_id, today, dismissed)
    found.sort(
        key=lambda c: (
            not c.needs_attention,
            STATUSES.index(c.status),
            -c.pattern.amount,
            c.merchant.lower(),
        )
    )
    return found


# A merchant with at least this many charges in the window but no steady
# cadence is *irregular* (a store, not a subscription).
IRREGULAR_MIN_COUNT = 3


@dataclass
class SeriesIndex:
    """What the link suggester needs from recurrence (issue #119): which
    series each transaction belongs to, and which merchants are known not to
    recur."""

    by_txn: dict[int, RecurringCharge] = field(default_factory=dict)
    # (merchant, kind) pairs with enough history to tell, and no cadence — or
    # merchants the user marked "not recurring".
    irregular: set[tuple[str, str]] = field(default_factory=set)
    dismissed: set[str] = field(default_factory=set)

    def series_of(self, txn: dict[str, Any]) -> RecurringCharge | None:
        return self.by_txn.get(txn["id"])

    def is_irregular(self, txn: dict[str, Any]) -> bool:
        key = series_key(txn)
        return key is not None and (key in self.irregular or key[0] in self.dismissed)


def series_index(conn: Connection, snapshot_id: int, *, today: date) -> SeriesIndex:
    """Recurring series as of ``today``, indexed for the link suggester."""
    dismissed = get_recurring_dismissals(conn, snapshot_id)
    found, counts = _scan(conn, snapshot_id, today, dismissed)
    recurring_keys = {(c.merchant, c.kind) for c in found}
    return SeriesIndex(
        by_txn={tid: c for c in found for tid in c.transaction_ids},
        irregular={
            key
            for key, n in counts.items()
            if n >= IRREGULAR_MIN_COUNT and key not in recurring_keys
        },
        dismissed=set(dismissed),
    )


def _usd(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def summary_line(charge: RecurringCharge) -> str:
    """One terse line for the Overview card and `status`: a price change
    ("Netflix: $15.49 → $17.99 since Oct") or a series not in the budget
    ("Spotify: $11.99/mo, not in budget"; income says "income")."""
    p = charge.pattern
    if p.price_change_since is not None and p.previous_amount is not None:
        return (
            f"{charge.merchant}: {_usd(p.previous_amount)} → {_usd(p.amount)} "
            f"since {p.price_change_since:%b}"
        )
    noun = "income" if charge.kind == "income" else ""
    tail = f"{noun + ' ' if noun else ''}not in budget"
    return f"{charge.merchant}: {_usd(p.amount)}{charge.cadence.suffix}, {tail}"


def attention_items(charges: list[RecurringCharge]) -> list[RecurringCharge]:
    """The series worth a line on the Overview: not in the budget, or a
    recent price change."""
    return [c for c in charges if c.needs_attention]


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


class NotRecurring(ValueError):
    """No recurring series for that merchant (and kind) in the snapshot."""


def get_recurring(
    conn: Connection,
    snapshot_id: int,
    merchant: str,
    kind: str = "expense",
    *,
    today: date | None = None,
) -> RecurringCharge:
    for charge in find_recurring(conn, snapshot_id, today=today):
        if charge.merchant == merchant and charge.kind == kind:
            return charge
    raise NotRecurring(f"No recurring {kind} found for {merchant!r}")


def entry_for(charge: RecurringCharge) -> dict[str, Any]:
    """The budget entry a series would become: its current price, cadence,
    day, category and usual account, planned from the month of its first
    charge (issue #136) so the charges it links fall in its months."""
    last = charge.pattern.last_date
    entry: dict[str, Any] = {
        "kind": charge.kind,
        "description": charge.merchant,
        "amount": charge.pattern.amount,
        "recurrence": charge.cadence.recurrence,
        "startMonth": charge.pattern.first_date.replace(day=1).isoformat(),
    }
    if charge.category:
        entry["category"] = charge.category
    if charge.account_id is not None:
        entry["autoAccountRef"] = charge.account_id
    if charge.cadence.key == "monthly":
        entry["dayOfMonth"] = last.day
    elif charge.cadence.key == "annual":
        entry["month"] = last.month
        entry["dayOfMonth"] = last.day
    return entry


def _link_series(
    conn: Connection, snapshot_id: int, charge: RecurringCharge, entry_ref: int
) -> int:
    linked = 0
    for txn_id in charge.transaction_ids:
        link_transaction(conn, snapshot_id, txn_id, entry_ref)
        linked += 1
    return linked


def add_to_budget(
    conn: Connection,
    snapshot_id: int,
    merchant: str,
    kind: str = "expense",
    *,
    today: date | None = None,
) -> int:
    """Create a budget entry for a recurring series (``entry_for``) and link
    its transactions to it. Returns the new entry's id. A recent price change
    becomes the entry's history: the previous price from the first charge,
    the current one from the month it changed."""
    charge = get_recurring(conn, snapshot_id, merchant, kind, today=today)
    entry = entry_for(charge)
    since = charge.pattern.price_change_since
    previous = charge.pattern.previous_amount
    split = (
        previous is not None
        and since is not None
        and since.replace(day=1) > charge.pattern.first_date.replace(day=1)
    )
    if split:
        entry["amount"] = previous
    entry_ref = add_budget_entry(conn, snapshot_id, entry)
    if split:
        update_budget_entry_by_id(
            conn,
            snapshot_id,
            entry_ref,
            {"amount": charge.pattern.amount},
            effective=since,
        )
    _link_series(conn, snapshot_id, charge, entry_ref)
    return entry_ref


def link_to_entry(
    conn: Connection,
    snapshot_id: int,
    merchant: str,
    entry_ref: int,
    kind: str = "expense",
    *,
    today: date | None = None,
) -> int:
    """Link every transaction of a recurring series to an existing entry
    (the ``unlinked`` case). Returns how many were linked."""
    charge = get_recurring(conn, snapshot_id, merchant, kind, today=today)
    return _link_series(conn, snapshot_id, charge, entry_ref)


def dismiss(conn: Connection, snapshot_id: int, merchant: str) -> None:
    """Mark a merchant "not recurring" in this snapshot."""
    add_recurring_dismissal(conn, snapshot_id, merchant)


def undismiss(conn: Connection, snapshot_id: int, merchant: str) -> bool:
    """Forget a "not recurring" mark; False if there wasn't one."""
    return remove_recurring_dismissal(conn, snapshot_id, merchant)
