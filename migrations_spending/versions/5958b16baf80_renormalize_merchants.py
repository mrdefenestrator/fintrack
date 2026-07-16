"""renormalize_merchants

Revision ID: 5958b16baf80
Revises: 8dd6f7111d99
Create Date: 2026-04-17 00:10:49.429485

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5958b16baf80'
down_revision: Union[str, Sequence[str], None] = '8dd6f7111d99'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from spending.importer.normalize import normalize_merchant, _load_config

    # Ensure the updated config is loaded (not a stale cached version)
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

    if not old_to_new:
        return

    # --- 2. Update merchant_cache using the old→new mapping ---
    # Process in id order so renames happen before the duplicate check
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
            # New name already in cache — drop the stale duplicate
            conn.execute(
                sa.text("DELETE FROM merchant_cache WHERE id = :id"),
                {"id": row.id},
            )
        else:
            # Rename to the new normalized name
            conn.execute(
                sa.text(
                    "UPDATE merchant_cache"
                    " SET merchant_name = :new, updated_at = CURRENT_TIMESTAMP"
                    " WHERE id = :id"
                ),
                {"new": new_name, "id": row.id},
            )


def downgrade() -> None:
    # Data migrations are not reversible — normalization changes can't be un-applied
    # without the original raw→normalized mapping for every affected row.
    raise NotImplementedError("Downgrade not supported for data migration 5958b16baf80")
