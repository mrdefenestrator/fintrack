"""balance_history for loans and assets: 'price' + 'baseline' sources

Revision ID: e7f8a9b0c1d2
Revises: d5e6f7a8b9c0
Create Date: 2026-11-07 00:00:00.000000

Issue #107: loans and assets now keep dated values in balance_history like
cash and credit cards, so Net worth can be read for any past date.

1. Two new sources: 'price' (a price-cache refresh re-valued a priced asset)
   and 'baseline' (the one-time starting point below). SQLite can't alter a
   CHECK, so the table is rebuilt.
2. Every loan and asset with no history yet gets one 'baseline' point at its
   current value, dated its As Of (or today), so reads of any later date have
   a value to carry forward. An asset's value is quantity x (cached price for
   a non-USD unit, else its manual value), matching the Holdings sheet.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | Sequence[str] | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_SOURCES = "'statement','manual','migration'"
_NEW_SOURCES = "'statement','manual','migration','price','baseline'"


def _rebuild(sources: str) -> None:
    op.execute(
        f"""
        CREATE TABLE balance_history_new (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL,
            as_of DATE NOT NULL,
            balance NUMERIC(14,2) NOT NULL,
            available NUMERIC(14,2),
            source VARCHAR NOT NULL,
            import_id INTEGER,
            note VARCHAR,
            created_at DATETIME,
            CONSTRAINT ck_balance_history_source CHECK (source IN ({sources})),
            CONSTRAINT uq_balance_history_point UNIQUE (account_id, as_of, source),
            FOREIGN KEY (account_id) REFERENCES holdings (id) ON DELETE CASCADE,
            FOREIGN KEY (import_id) REFERENCES imports (id)
        )
        """
    )
    op.execute(
        "INSERT INTO balance_history_new "
        "SELECT id, account_id, as_of, balance, available, source, import_id, "
        "note, created_at FROM balance_history"
    )
    op.execute("DROP TABLE balance_history")
    op.execute("ALTER TABLE balance_history_new RENAME TO balance_history")


def upgrade() -> None:
    _rebuild(_NEW_SOURCES)

    no_history = (
        "NOT EXISTS (SELECT 1 FROM balance_history b WHERE b.account_id = h.id)"
    )
    today = "date('now', 'localtime')"
    op.execute(
        f"""
        INSERT INTO balance_history (account_id, as_of, balance, source, created_at)
        SELECT h.id, COALESCE(h.as_of_date, {today}), l.balance, 'baseline',
               CURRENT_TIMESTAMP
        FROM holdings h JOIN loan_details l ON l.holding_id = h.id
        WHERE l.balance IS NOT NULL AND {no_history}
        """
    )
    op.execute(
        f"""
        INSERT INTO balance_history (account_id, as_of, balance, source, created_at)
        SELECT h.id, COALESCE(h.as_of_date, {today}),
               ROUND(COALESCE(a.quantity, 1) * CASE
                   WHEN a.unit != 'USD' AND p.price_usd IS NOT NULL
                       THEN p.price_usd
                   ELSE COALESCE(a.value, 0)
               END, 2),
               'baseline', CURRENT_TIMESTAMP
        FROM holdings h
        JOIN asset_details a ON a.holding_id = h.id
        LEFT JOIN price_cache p ON p.unit = a.unit
        WHERE {no_history}
        """
    )
    # Keep the spine As Of a cache of the latest point (it was NULL for some).
    op.execute(
        f"""
        UPDATE holdings SET as_of_date = {today}
        WHERE as_of_date IS NULL AND group_key IN ('loan', 'asset')
          AND id IN (SELECT account_id FROM balance_history
                     WHERE source = 'baseline')
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM balance_history WHERE source IN ('price', 'baseline')")
    _rebuild(_OLD_SOURCES)
