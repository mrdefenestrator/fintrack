"""add budget_entry_ref to transaction_corrections

Revision ID: a2b3c4d5e6f7
Revises: f1e2d3c4b5a6
Create Date: 2026-09-06 00:00:00.000000

Issue #53: associate individual transactions with budget entries. The link
lives on the corrections overlay (transactions stay immutable) as a nullable
FK to budget_entries with ON DELETE SET NULL, so deleting a budget entry
unlinks its transactions rather than deleting them. SQLite recreates the table
via batch_alter_table, preserving the existing transaction_id FK/uniqueness.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = "f1e2d3c4b5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("transaction_corrections") as batch_op:
        batch_op.add_column(sa.Column("budget_entry_ref", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_correction_budget_entry",
            "budget_entries",
            ["budget_entry_ref"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("transaction_corrections") as batch_op:
        batch_op.drop_constraint("fk_correction_budget_entry", type_="foreignkey")
        batch_op.drop_column("budget_entry_ref")
