"""drop partial_account_number and relax accounts unique constraint

partial_account_number is dropped entirely (issue 0b): before dropping the
column, fold any non-empty value into the account name (" [<partial>]") so
the information isn't silently lost, disambiguating against the new unique
constraint if that produces a collision.

The accounts unique constraint moves from (snapshot_id, name) to
(snapshot_id, institution, name) (issue 1), so e.g. a "Wallet" account from
Venmo and a "Wallet" account from PayPal can coexist.

Both changes are done together in one SQLite batch-mode table rebuild.

Revision ID: b0b3c9940bc5
Revises: 6a88702b7507
Create Date: 2026-07-17 01:04:48.655235

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b0b3c9940bc5'
down_revision: Union[str, Sequence[str], None] = '6a88702b7507'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()

    # --- fold partial_account_number into name before the column is dropped ---
    rows = conn.execute(
        sa.text(
            "SELECT id, snapshot_id, institution, name, partial_account_number "
            "FROM accounts"
        )
    ).fetchall()

    # Track (snapshot_id, institution, name) keys already in use so an
    # appended name can't collide with an existing one under the new
    # constraint (NULL institution compares as distinct in SQLite, same as
    # everywhere else here).
    existing_keys = {(r.snapshot_id, r.institution, r.name) for r in rows}

    for r in rows:
        partial = (r.partial_account_number or "").strip()
        if not partial:
            continue
        if partial in (r.name or ""):
            continue  # already folded in manually

        candidate = f"{r.name} [{partial}]"
        key = (r.snapshot_id, r.institution, candidate)
        suffix = 2
        while key in existing_keys:
            candidate = f"{r.name} [{partial}] ({suffix})"
            key = (r.snapshot_id, r.institution, candidate)
            suffix += 1

        existing_keys.discard((r.snapshot_id, r.institution, r.name))
        existing_keys.add(key)
        conn.execute(
            sa.text("UPDATE accounts SET name = :name WHERE id = :id"),
            {"name": candidate, "id": r.id},
        )

    # --- schema changes: drop the column, relax the unique constraint ---
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_constraint("uq_accounts_snapshot_name", type_="unique")
        batch_op.drop_column("partial_account_number")
        batch_op.create_unique_constraint(
            "uq_accounts_snapshot_institution_name",
            ["snapshot_id", "institution", "name"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_accounts_snapshot_institution_name", type_="unique"
        )
        batch_op.add_column(
            sa.Column("partial_account_number", sa.String(), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_accounts_snapshot_name", ["snapshot_id", "name"]
        )
