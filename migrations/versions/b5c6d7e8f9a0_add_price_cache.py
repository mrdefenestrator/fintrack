"""add price_cache table for external price lookups

Global cache of fetched asset prices (crypto, stocks) keyed by unit
symbol.  Prices are auto-refreshed from CoinCap (crypto) and Yahoo
Finance (stocks) when stale; the per-row asset_entries.value field
remains as the offline / unknown-ticker fallback.

Revision ID: b5c6d7e8f9a0
Revises: a70203be0102
Create Date: 2026-08-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, Sequence[str], None] = "a70203be0102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "price_cache",
        sa.Column("unit", sa.String, primary_key=True),
        sa.Column("price_usd", sa.Numeric(14, 6), nullable=False),
        sa.Column("fetched_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("price_cache")
