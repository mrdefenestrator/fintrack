"""budget entries: start/end months + dated versions

Revision ID: a8b9c0d1e2f3
Revises: e6f7a8b9c0d1
Create Date: 2026-11-13 00:00:00.000000

Issue #136: the budget keeps its history instead of being one timeless plan.

- ``budget_entries`` gains ``start_month`` / ``end_month`` (the 1st of the
  first and last month the entry is in the plan; NULL = unbounded). Existing
  entries keep NULL for both, so they still apply to every month, as before.
- ``budget_entry_versions`` holds dated changes to an entry's amount and
  schedule, each in effect from its ``effective_from`` month until the next.
  The schedule columns on ``budget_entries`` stay as the entry's base version,
  so existing rows need no data migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: str | Sequence[str] | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RECURRENCES = (
    "'one_time', 'monthly', 'weekly', 'biweekly', 'quarterly', 'semiannual', 'annual'"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # The app runs metadata.create_all() on startup: it can create the new
    # table, but never adds columns to an existing one.
    columns = {c["name"] for c in inspector.get_columns("budget_entries")}
    for name in ("start_month", "end_month"):
        if name not in columns:
            op.add_column("budget_entries", sa.Column(name, sa.Date(), nullable=True))
    if inspector.has_table("budget_entry_versions"):
        return
    op.create_table(
        "budget_entry_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entry_id",
            sa.Integer(),
            sa.ForeignKey("budget_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("recurrence", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("month", sa.Integer(), nullable=True),
        sa.Column("day_of_year", sa.Integer(), nullable=True),
        sa.Column("continuous", sa.Boolean(), nullable=True),
        sa.CheckConstraint(
            f"recurrence IN ({_RECURRENCES})", name="ck_budget_version_recurrence"
        ),
        sa.UniqueConstraint(
            "entry_id", "effective_from", name="uq_budget_version_month"
        ),
    )


def downgrade() -> None:
    # Check before changing anything: SQLite DDL isn't rolled back, so a
    # refusal after the drop would leave the database half-downgraded.
    bind = op.get_bind()
    versions = bind.exec_driver_sql(
        "SELECT count(*) FROM budget_entry_versions"
    ).scalar()
    dated = bind.exec_driver_sql(
        "SELECT count(*) FROM budget_entries"
        " WHERE start_month IS NOT NULL OR end_month IS NOT NULL"
    ).scalar()
    if versions or dated:
        raise RuntimeError(
            f"{versions} budget versions and {dated} dated budget entries exist;"
            " downgrading would lose budget history"
        )
    op.drop_table("budget_entry_versions")
    # Native DROP COLUMN (SQLite 3.35+), not a batch rebuild: dropping and
    # recreating budget_entries would SET NULL every transaction's budget link.
    bind.exec_driver_sql("ALTER TABLE budget_entries DROP COLUMN end_month")
    bind.exec_driver_sql("ALTER TABLE budget_entries DROP COLUMN start_month")
