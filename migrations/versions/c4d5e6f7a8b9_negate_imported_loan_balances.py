"""negate positive imported loan balances

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-10-23 00:00:00.000000

Issue #79: loan servicers report the principal owed as a positive OFX
LEDGERBAL, and imports stored it verbatim, so imported loans showed a positive
(asset-like) balance. The canonical signed balance is negative = owed, and
create_import now normalizes loan statement balances to -abs(). This repairs
data written before that fix:

1. imports.ledger_balance on loan imports -> -abs().
2. balance_history statement points on loan holdings -> -abs(). Manual and
   migration points are left alone (they were entered signed).
3. Re-sync each loan's cached loan_details.balance to its latest history
   point, as record_balance() would.

downgrade() is a no-op: the positive values were the bug.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE imports SET ledger_balance = -ledger_balance
        WHERE holding_group = 'loan' AND ledger_balance > 0
        """
    )
    op.execute(
        """
        UPDATE balance_history SET balance = -balance
        WHERE source = 'statement' AND balance > 0
          AND account_id IN (SELECT id FROM holdings WHERE group_key = 'loan')
        """
    )
    op.execute(
        """
        UPDATE loan_details SET balance = (
            SELECT bh.balance FROM balance_history bh
            WHERE bh.account_id = loan_details.holding_id
            ORDER BY bh.as_of DESC, bh.id DESC
            LIMIT 1
        )
        WHERE holding_id IN (SELECT account_id FROM balance_history)
        """
    )


def downgrade() -> None:
    pass
