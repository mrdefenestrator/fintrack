"""add transactions.external_id and transaction_duplicates

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-10-23 00:00:00.000000

Issue #82: duplicate detection only matched on (date, amount, raw description),
so re-exports whose descriptions changed imported as new rows.

1. transactions.external_id keeps the institution's own transaction id (OFX
   FITID); a match within the account is a definite duplicate. Existing rows
   stay NULL — the ids can't be recovered without the original files.
2. transaction_duplicates holds near-duplicate flags and the user's
   resolution of them, as an overlay so raw transactions stay immutable.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("external_id", sa.String(), nullable=True))
    op.create_index(
        "ix_transactions_account_external_id",
        "transactions",
        ["account_id", "external_id"],
    )
    op.create_table(
        "transaction_duplicates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "transaction_id",
            sa.Integer(),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "duplicate_of_id",
            sa.Integer(),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('flagged', 'duplicate', 'not_duplicate')",
            name="ck_txn_duplicate_status",
        ),
        sa.CheckConstraint(
            "source IN ('import', 'scan')", name="ck_txn_duplicate_source"
        ),
        sa.CheckConstraint(
            "transaction_id != duplicate_of_id", name="ck_txn_duplicate_not_self"
        ),
    )
    op.create_index(
        "ix_transaction_duplicates_of", "transaction_duplicates", ["duplicate_of_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_transaction_duplicates_of", "transaction_duplicates")
    op.drop_table("transaction_duplicates")
    op.drop_index("ix_transactions_account_external_id", "transactions")
    # Native DROP COLUMN (SQLite >= 3.35) rather than a batch table rebuild,
    # which would cascade-delete corrections under foreign_keys=ON.
    op.execute("ALTER TABLE transactions DROP COLUMN external_id")
