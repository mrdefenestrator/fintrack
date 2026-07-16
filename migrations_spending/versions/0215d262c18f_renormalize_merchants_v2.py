"""renormalize_merchants_v2

Revision ID: 0215d262c18f
Revises: 5958b16baf80
Create Date: 2026-04-17 00:26:16.706637

Applies updated normalization config:
- New prefix: CLCKPAY*
- New leading_patterns: strip leading numeric location codes
- New trailing patterns: Lyft ride stamps, NAME* DESCRIPTION, iPhone installments,
  eBay order numbers, orphan #, trailing dash
- New aliases section: consolidates duplicates (Disney+, Nintendo, Banfield,
  Saigon Basil, Matsuhisa, Amazon, Starbucks, Dutch Bros, eBay, Southwest)
- Category fix: ANTHROPIC → Subscriptions
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0215d262c18f'
down_revision: Union[str, Sequence[str], None] = '5958b16baf80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from spending.importer.normalize import normalize_merchant, _load_config

    _load_config.cache_clear()

    conn = op.get_bind()

    # --- 1. Re-normalize all transactions, build old→new name mapping ---
    rows = conn.execute(
        sa.text("SELECT id, raw_description, normalized_merchant FROM transactions")
    ).fetchall()

    old_to_new: dict[str, str] = {}
    for row in rows:
        new_name = normalize_merchant(row.raw_description)
        if new_name != row.normalized_merchant:
            old_to_new[row.normalized_merchant] = new_name
            conn.execute(
                sa.text("UPDATE transactions SET normalized_merchant = :new WHERE id = :id"),
                {"new": new_name, "id": row.id},
            )

    # --- 2. Update merchant_cache using the old→new mapping ---
    cache_rows = conn.execute(
        sa.text("SELECT id, merchant_name FROM merchant_cache ORDER BY id")
    ).fetchall()

    for row in cache_rows:
        new_name = old_to_new.get(row.merchant_name)
        if new_name is None:
            continue

        existing = conn.execute(
            sa.text("SELECT id FROM merchant_cache WHERE merchant_name = :name"),
            {"name": new_name},
        ).fetchone()

        if existing:
            conn.execute(
                sa.text("DELETE FROM merchant_cache WHERE id = :id"),
                {"id": row.id},
            )
        else:
            conn.execute(
                sa.text(
                    "UPDATE merchant_cache"
                    " SET merchant_name = :new, updated_at = CURRENT_TIMESTAMP"
                    " WHERE id = :id"
                ),
                {"new": new_name, "id": row.id},
            )

    # --- 3. Fix known category anomalies ---
    conn.execute(
        sa.text(
            "UPDATE merchant_cache"
            " SET category = 'Subscriptions', updated_at = CURRENT_TIMESTAMP"
            " WHERE merchant_name = 'ANTHROPIC'"
        )
    )


def downgrade() -> None:
    raise NotImplementedError("Downgrade not supported for data migration 0215d262c18f")
