"""add asset_entries.unit for qty denomination

Introduces the `unit` column on `asset_entries`: the denomination of a row's
quantity. "USD" (the default) means the price is 1, so amount == quantity — the
behavior every existing row already has. A ticker/symbol unit (AAPL, BTC, …)
means the per-unit price comes from the price cache and amount = quantity *
unit price (a later feature).

Existing rows get "USD" via the column's server default, preserving today's
dollar-denominated amounts exactly.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-07-20 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "asset_entries",
        sa.Column("unit", sa.String(), nullable=False, server_default="USD"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("asset_entries", "unit")
