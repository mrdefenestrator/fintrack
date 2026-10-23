"""add link_suggestion_dismissals

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-10-23 00:00:00.000000

Issue #70: remember transaction↔budget-entry link suggestions the user
dismissed, so the suggester stops proposing them. One row per rejected
(transaction, entry) pair; both FKs cascade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "link_suggestion_dismissals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "transaction_id",
            sa.Integer(),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "budget_entry_ref",
            sa.Integer(),
            sa.ForeignKey("budget_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "transaction_id", "budget_entry_ref", name="uq_link_dismissal_pair"
        ),
    )


def downgrade() -> None:
    op.drop_table("link_suggestion_dismissals")
