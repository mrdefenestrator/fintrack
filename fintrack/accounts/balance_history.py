"""Balance history: the dated value of every holding.

Every value write lands here — statement imports ('statement'), manual edits
('manual'), the one-time legacy migration ('migration'), price refreshes that
re-value a priced asset ('price'), and the one-time starting point for loans
and assets ('baseline'). `balance` is signed like the detail balances
(negative = owed on cards and loans); for an asset it is the holding's value
(quantity x unit price). (`account_id` names the holding; the column keeps its
historical name.)

The holding's detail-row balance (cash, cards, loans; assets have none) and
its spine as_of_date are a denormalized cache of the latest point, re-synced
inside the same transaction as the write. Read a past value with
`balances_on()`: the latest point on or before a date.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from fintrack.core.holdings import DETAIL_TABLES
from fintrack.core.models import (
    balance_history,
    credit_card_details,
    holdings,
    imports,
    transactions,
)
from fintrack.ledger.repository.duplicates import not_hidden_duplicate

RECONCILE_TOLERANCE = Decimal("0.005")


def record_balance(
    conn: Connection,
    *,
    account_id: int,
    balance: Decimal,
    as_of: date | None = None,
    source: str = "manual",
    available: Decimal | None = None,
    import_id: int | None = None,
    note: str | None = None,
) -> None:
    """Upsert one balance point and re-sync the holding row to the latest.

    Two writes for the same (holding, day, source) collapse to the newer
    values; a manual edit and a statement on the same day coexist.

    An asset keeps one point per day instead: its value comes from one place
    (quantity x price), so a second write that day updates the first. A price
    refresh keeps the day's existing source; a manual edit marks it 'manual'.
    """
    as_of = as_of or datetime.now().astimezone().date()
    group = conn.execute(
        select(holdings.c.group_key).where(holdings.c.id == account_id)
    ).scalar()
    same_day = None
    if group == "asset":
        same_day = conn.execute(
            select(balance_history.c.id, balance_history.c.source).where(
                balance_history.c.account_id == account_id,
                balance_history.c.as_of == as_of,
            )
        ).first()
    if same_day is not None:
        conn.execute(
            update(balance_history)
            .where(balance_history.c.id == same_day.id)
            .values(
                balance=balance,
                source=same_day.source if source == "price" else source,
                note=note,
            )
        )
    elif group == "asset":
        conn.execute(
            insert(balance_history).values(
                account_id=account_id,
                as_of=as_of,
                balance=balance,
                source=source,
                note=note,
            )
        )
    else:
        conn.execute(
            sqlite_insert(balance_history)
            .values(
                account_id=account_id,
                as_of=as_of,
                balance=balance,
                available=available,
                source=source,
                import_id=import_id,
                note=note,
            )
            .on_conflict_do_update(
                index_elements=["account_id", "as_of", "source"],
                set_={
                    "balance": balance,
                    "available": available,
                    "import_id": import_id,
                    "note": note,
                },
            )
        )
    _sync_account_to_latest(conn, account_id)
    conn.commit()


def _sync_account_to_latest(conn: Connection, account_id: int) -> None:
    # A price refresh re-values an asset but says nothing about when the user
    # last checked it, so 'price' points don't move As Of. (Only assets get
    # them, and assets have no cached balance, so nothing else is skipped.)
    latest = (
        conn.execute(
            select(balance_history)
            .where(
                balance_history.c.account_id == account_id,
                balance_history.c.source != "price",
            )
            .order_by(balance_history.c.as_of.desc(), balance_history.c.id.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if latest is None:
        return
    holding = conn.execute(
        select(holdings.c.group_key).where(holdings.c.id == account_id)
    ).first()
    if holding is None:
        return
    conn.execute(
        update(holdings)
        .where(holdings.c.id == account_id)
        .values(as_of_date=latest["as_of"])
    )
    detail = DETAIL_TABLES[holding.group_key]
    if "balance" not in detail.c:
        # Asset holdings have no cached balance column: their value is
        # computed (quantity x price); the spine as_of still moved.
        return
    values: dict[str, Any] = {"balance": latest["balance"]}
    if holding.group_key == "credit_card" and latest["available"] is not None:
        # A statement that reports available credit lets us fill an unset
        # credit limit (limit = available - signed balance). Never overwrite
        # a user-set limit; available itself is computed, not stored.
        credit_limit = conn.execute(
            select(credit_card_details.c.credit_limit).where(
                credit_card_details.c.holding_id == account_id
            )
        ).scalar()
        if credit_limit is None:
            values["credit_limit"] = latest["available"] - latest["balance"]
    conn.execute(
        update(detail).where(detail.c.holding_id == account_id).values(**values)
    )


def get_balance_history(
    conn: Connection, account_id: int, limit: int | None = None
) -> list[dict[str, Any]]:
    """History points for one account, oldest first (for sparklines)."""
    stmt = (
        select(balance_history)
        .where(balance_history.c.account_id == account_id)
        .order_by(balance_history.c.as_of.desc(), balance_history.c.id.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = [dict(r) for r in conn.execute(stmt).mappings().all()]
    rows.reverse()
    return rows


def balances_on(
    conn: Connection, account_ids: list[int], on: date
) -> dict[int, Decimal]:
    """Each holding's balance as of `on`: its latest history point dated on or
    before that day. Holdings with no such point are left out."""
    if not account_ids:
        return {}
    stmt = (
        select(balance_history.c.account_id, balance_history.c.balance)
        .where(
            balance_history.c.account_id.in_(account_ids),
            balance_history.c.as_of <= on,
        )
        .order_by(
            balance_history.c.account_id,
            balance_history.c.as_of,
            balance_history.c.id,
        )
    )
    out: dict[int, Decimal] = {}
    for account_id, balance in conn.execute(stmt):
        out[account_id] = Decimal(balance)  # later rows overwrite earlier ones
    return out


def latest_point(conn: Connection, account_id: int) -> dict[str, Any] | None:
    rows = get_balance_history(conn, account_id, limit=1)
    return rows[-1] if rows else None


def reconciliation_note(
    conn: Connection,
    *,
    account_id: int,
    statement_balance: Decimal,
    as_of: date,
) -> str | None:
    """Informational check: does the previous balance plus the confirmed
    transactions in between land on the statement balance?

    Returns a note describing the delta when it doesn't, None when it does
    (or when there is no earlier point to reconcile against).
    """
    previous = (
        conn.execute(
            select(balance_history)
            .where(
                balance_history.c.account_id == account_id,
                balance_history.c.as_of < as_of,
            )
            .order_by(balance_history.c.as_of.desc(), balance_history.c.id.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if previous is None:
        return None
    txn_sum = conn.execute(
        select(func.coalesce(func.sum(transactions.c.amount), 0))
        .select_from(
            transactions.join(imports, transactions.c.import_id == imports.c.id)
        )
        .where(
            transactions.c.account_id == account_id,
            transactions.c.date > previous["as_of"],
            transactions.c.date <= as_of,
            imports.c.status == "confirmed",
            not_hidden_duplicate(),
        )
    ).scalar()
    expected = previous["balance"] + Decimal(str(txn_sum))
    delta = statement_balance - expected
    if abs(delta) <= RECONCILE_TOLERANCE:
        return None
    return (
        f"unreconciled: expected {expected} "
        f"({previous['balance']} on {previous['as_of']} + {txn_sum} in transactions), "
        f"statement says {statement_balance} (delta {delta:+})"
    )
