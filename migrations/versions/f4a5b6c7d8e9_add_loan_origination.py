"""add loan origination data and normalize debt due day

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-07-25 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "asset_entries",
        sa.Column("original_principal", sa.Numeric(14, 2), nullable=True),
    )
    op.add_column(
        "asset_entries", sa.Column("term_months", sa.Integer(), nullable=True)
    )
    op.add_column(
        "asset_entries", sa.Column("origination_date", sa.Date(), nullable=True)
    )
    op.add_column(
        "asset_entries",
        sa.Column("statement_due_day_of_month", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE asset_entries
        SET statement_due_day_of_month =
            CAST(strftime('%d', next_due_date) AS INTEGER)
        WHERE next_due_date IS NOT NULL
        """
    )
    with op.batch_alter_table("asset_entries") as batch_op:
        batch_op.drop_column("next_due_date")


def downgrade() -> None:
    op.add_column(
        "asset_entries", sa.Column("next_due_date", sa.Date(), nullable=True)
    )
    with op.batch_alter_table("asset_entries") as batch_op:
        batch_op.drop_column("statement_due_day_of_month")
        batch_op.drop_column("origination_date")
        batch_op.drop_column("term_months")
        batch_op.drop_column("original_principal")
