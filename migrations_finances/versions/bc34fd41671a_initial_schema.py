"""initial_schema

Revision ID: bc34fd41671a
Revises:
Create Date: 2026-05-29 15:43:45.331391

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "bc34fd41671a"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Parent tables first so foreign keys resolve. snapshot_id uses ON DELETE
    # CASCADE; the cross-reference FKs use the default (NO ACTION) so a snapshot
    # cascade can delete referenced rows while direct deletes stay blocked.
    op.create_table(
        "fin_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "fin_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("balance", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("limit", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("available", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("rewards_balance", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column(
            "statement_balance", sa.Numeric(precision=12, scale=2), nullable=True
        ),
        sa.Column("statement_due_day_of_month", sa.Integer(), nullable=True),
        sa.Column("payment_account_ref", sa.Integer(), nullable=True),
        sa.Column("as_of_date", sa.String(), nullable=True),
        sa.Column("minimum_balance", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("institution", sa.String(), nullable=True),
        sa.Column("partial_account_number", sa.String(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["fin_snapshots.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["payment_account_ref"], ["fin_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "fin_budget_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("recurrence", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("date", sa.String(), nullable=True),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("month", sa.Integer(), nullable=True),
        sa.Column("day_of_year", sa.Integer(), nullable=True),
        sa.Column("continuous", sa.Boolean(), nullable=True),
        sa.Column("auto_account_ref", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["fin_snapshots.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["auto_account_ref"], ["fin_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "fin_asset_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("institution", sa.String(), nullable=True),
        sa.Column("value", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("balance", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("asset_ref", sa.Integer(), nullable=True),
        sa.Column("interest_rate", sa.Numeric(precision=8, scale=6), nullable=True),
        sa.Column("next_due_date", sa.String(), nullable=True),
        sa.Column("as_of_date", sa.String(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["fin_snapshots.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["asset_ref"], ["fin_asset_entries.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("fin_asset_entries")
    op.drop_table("fin_budget_entries")
    op.drop_table("fin_accounts")
    op.drop_table("fin_snapshots")
