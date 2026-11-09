"""weekly budget recurrence + recurring_dismissals

Revision ID: e6f7a8b9c0d1
Revises: f2a3b4c5d6e7
Create Date: 2026-11-09 00:00:00.000000

Issue #100 (recurring charges not in the budget):

- ``budget_entries.recurrence`` gains ``weekly``, so a detected weekly charge
  can become a budget entry. SQLite can't alter a CHECK constraint, so the
  table is rebuilt from its own stored DDL with only the recurrence list
  changed. FK enforcement is switched off for the swap: with it on, dropping
  the old table would SET NULL every transaction's budget link and cascade
  away link-suggestion dismissals.
- ``recurring_dismissals`` remembers merchants the user marked "not
  recurring", per snapshot.
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WITH_WEEKLY = (
    "'one_time', 'monthly', 'weekly', 'biweekly', 'quarterly', 'semiannual', 'annual'"
)
_WITHOUT_WEEKLY = (
    "'one_time', 'monthly', 'biweekly', 'quarterly', 'semiannual', 'annual'"
)
# The recurrence CHECK's value list, however it was spaced or wrapped when
# created (the baseline and the holdings split wrote it differently).
_RECURRENCE_CHECK = re.compile(r"(recurrence\s+IN\s*\()([^)]*)(\))", re.IGNORECASE)


def _rebuild_budget_entries(values: str) -> None:
    bind = op.get_bind()
    ddl = bind.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='budget_entries'"
    ).scalar()
    new_ddl, n = _RECURRENCE_CHECK.subn(rf"\g<1>{values}\g<3>", ddl)
    if n != 1:
        raise RuntimeError("budget_entries recurrence CHECK not found")
    if new_ddl == ddl:
        return
    new_ddl = re.sub(
        r"CREATE TABLE\s+\"?budget_entries\"?",
        "CREATE TABLE budget_entries_new",
        new_ddl,
        count=1,
    )
    fk_was_on = bool(bind.exec_driver_sql("PRAGMA foreign_keys").scalar())
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    bind.exec_driver_sql(new_ddl)
    bind.exec_driver_sql("INSERT INTO budget_entries_new SELECT * FROM budget_entries")
    bind.exec_driver_sql("DROP TABLE budget_entries")
    bind.exec_driver_sql("ALTER TABLE budget_entries_new RENAME TO budget_entries")
    issues = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if issues:
        raise RuntimeError(f"foreign_key_check failed: {issues[:5]}")
    if fk_was_on:
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def upgrade() -> None:
    _rebuild_budget_entries(_WITH_WEEKLY)
    # The app runs metadata.create_all() on startup, so the table may exist.
    if sa.inspect(op.get_bind()).has_table("recurring_dismissals"):
        return
    op.create_table(
        "recurring_dismissals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("merchant", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("snapshot_id", "merchant", name="uq_recurring_dismissal"),
    )


def downgrade() -> None:
    # Check before changing anything: SQLite DDL isn't rolled back, so a
    # refusal after the drop would leave the database half-downgraded.
    weekly = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT count(*) FROM budget_entries WHERE recurrence='weekly'"
        )
        .scalar()
    )
    if weekly:
        raise RuntimeError(
            f"{weekly} weekly budget entries exist; change or delete them first"
        )
    op.drop_table("recurring_dismissals")
    _rebuild_budget_entries(_WITHOUT_WEEKLY)
