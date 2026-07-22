"""retire catch-all asset types (other_asset, crypto)

The holding-type vocabulary drops the catch-all `other_asset` and the `crypto`
type (crypto is a digital wallet denominated in a symbol unit, not a type).

- `other_asset` -> NULL: these rows are genuinely unclassified and only the user
  can say whether each is retirement / real estate / vehicle / etc. NULL renders
  as "—" and prompts reclassification (tier falls back to illiquid meanwhile, so
  net worth is unaffected).
- `crypto` -> `digital_wallet`: a safe, defined rename (the symbol unit + the
  non-USD liquidity cap now carry what `crypto` used to mean). No-op if absent.

Accounts are untouched: no "other" account type exists in practice, and
account_type is NOT NULL.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-07-20 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    conn.execute(
        sa.text("UPDATE asset_entries SET type = NULL WHERE type = 'other_asset'")
    )
    conn.execute(
        sa.text(
            "UPDATE asset_entries SET type = 'digital_wallet' WHERE type = 'crypto'"
        )
    )


def downgrade() -> None:
    """No-op: not cleanly reversible.

    Once other_asset rows are NULLed (and possibly reclassified), the original
    values can't be recovered, and NULL is also the legitimate "unclassified"
    state for newly created holdings — so relabeling NULL -> other_asset here
    would corrupt those. Leave the data as-is.
    """
    pass
