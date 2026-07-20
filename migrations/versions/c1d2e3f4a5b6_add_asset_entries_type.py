"""add asset_entries.type for liquidity-tier classification

Introduces the `type` column on `asset_entries` (see
fintrack.core.types.ASSET_TYPE_TIER). It subtypes the bare asset/debt `kind`
so every holding can be placed in a liquidity tier (liquid / semi-liquid /
illiquid) for the unified holdings totals.

Existing rows are backfilled from `kind`:
  - kind == "debt"  -> "loan"        (illiquid liability)
  - kind == "asset" -> "other_asset" (illiquid asset)

These are safe, net-worth-preserving defaults; the user re-types semi-liquid
holdings (brokerage / crypto / HSA) in the UI, which only changes which tier
they land in, never the net-worth total.

Revision ID: c1d2e3f4a5b6
Revises: b0b3c9940bc5
Create Date: 2026-07-20 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "b0b3c9940bc5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("asset_entries", sa.Column("type", sa.String(), nullable=True))
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE asset_entries SET type = 'loan' WHERE kind = 'debt'"))
    conn.execute(
        sa.text("UPDATE asset_entries SET type = 'other_asset' WHERE kind = 'asset'")
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("asset_entries", "type")
