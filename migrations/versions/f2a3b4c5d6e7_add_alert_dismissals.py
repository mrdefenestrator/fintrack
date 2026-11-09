"""add alert_dismissals

Revision ID: f2a3b4c5d6e7
Revises: e7f8a9b0c1d2
Create Date: 2026-11-09 00:00:00.000000

Issue #101: remember dismissed spending alerts. A row is either one
transaction (every alert on it) or one category's "running hot" alert for one
month. Both the snapshot and transaction FKs cascade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_dismissals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "transaction_id",
            sa.Integer(),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("month", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "(transaction_id IS NOT NULL AND category IS NULL AND month IS NULL)"
            " OR (transaction_id IS NULL AND category IS NOT NULL"
            " AND month IS NOT NULL)",
            name="ck_alert_dismissal_target",
        ),
        sa.UniqueConstraint(
            "snapshot_id", "transaction_id", name="uq_alert_dismissal_transaction"
        ),
        sa.UniqueConstraint(
            "snapshot_id", "category", "month", name="uq_alert_dismissal_category"
        ),
    )


def downgrade() -> None:
    op.drop_table("alert_dismissals")
