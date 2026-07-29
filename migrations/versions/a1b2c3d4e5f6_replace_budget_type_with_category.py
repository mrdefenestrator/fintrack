"""replace budget type enum with category

Revision ID: a1b2c3d4e5f6
Revises: f4a5b6c7d8e9
Create Date: 2026-07-29 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TYPE_TO_CATEGORY = {
    "housing": "Housing",
    "transport": "Transport",
    "insurance": "Insurance",
    "utility": "Utilities",
    "food": "Groceries",
    "product": "Shopping",
    "service": "Subscriptions",
    "salary": "Income",
    "bonus": "Income",
    "refund": "Income",
    "remittance": "Income",
}

_CATEGORY_TO_TYPE = {
    "Housing": "housing",
    "Transport": "transport",
    "Insurance": "insurance",
    "Utilities": "utility",
    "Groceries": "food",
    "Shopping": "product",
    "Subscriptions": "service",
    "Income": "salary",
}


def upgrade() -> None:
    for old_type, new_cat in _TYPE_TO_CATEGORY.items():
        op.execute(
            sa.text(
                "UPDATE budget_entries SET category = :cat "
                "WHERE type = :typ AND category IS NULL"
            ).bindparams(cat=new_cat, typ=old_type)
        )

    with op.batch_alter_table("budget_entries") as batch_op:
        batch_op.drop_column("type")


def downgrade() -> None:
    with op.batch_alter_table("budget_entries") as batch_op:
        batch_op.add_column(sa.Column("type", sa.String(), nullable=True))

    for cat, old_type in _CATEGORY_TO_TYPE.items():
        op.execute(
            sa.text(
                "UPDATE budget_entries SET type = :typ "
                "WHERE category = :cat AND type IS NULL"
            ).bindparams(typ=old_type, cat=cat)
        )
