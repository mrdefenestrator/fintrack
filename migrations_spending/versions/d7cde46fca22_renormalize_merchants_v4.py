"""renormalize_merchants_v4

Revision ID: d7cde46fca22
Revises: 22b2e31cb7ab
Create Date: 2026-04-17 00:52:52.271404

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7cde46fca22'
down_revision: Union[str, Sequence[str], None] = '22b2e31cb7ab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from spending.importer.normalize import normalize_merchant, _load_config

    _load_config.cache_clear()

    conn = op.get_bind()

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


def downgrade() -> None:
    raise NotImplementedError("Downgrade not supported for data migration d7cde46fca22")
