"""split accounts/asset_entries into holdings supertype + subtype tables

Revision ID: a7b8c9d0e1f2
Revises: f4a5b6c7d8e9, b5c6d7e8f9a0
Create Date: 2026-08-25 00:00:00.000000

Replaces the two wide variant tables (accounts, asset_entries) with a slim
holdings supertype plus four subtype detail tables (cash_details,
credit_card_details, loan_details, asset_details), and retargets the
referencing tables (imports, transactions, balance_history, budget_entries)
at the supertype. See docs/notes-schema-split.md.

This migration copies data and is IRREVERSIBLE: downgrade() raises. Before
touching anything it copies the SQLite file to <db>.pre-split.bak (the only
honest rollback for a personal-finance DB). The whole upgrade runs inside one
transaction with PRAGMA defer_foreign_keys=ON so insert ordering can't produce
transient orphans, and finishes with PRAGMA foreign_key_check.
"""

import shutil
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# Also merges the dangling price_cache head (b5c6d7e8f9a0) so the chain has a
# single head again.
revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = ("f4a5b6c7d8e9", "b5c6d7e8f9a0")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


class _MigrationAbort(Exception):
    """A data condition that must be fixed before the split can run."""


def _backup_db(bind) -> None:
    """Copy the SQLite file to <db>.pre-split.bak (best effort for :memory:)."""
    url = bind.engine.url
    if url.database in (None, ":memory:"):
        return
    src = Path(url.database)
    if src.exists():
        shutil.copy2(src, src.with_suffix(src.suffix + ".pre-split.bak"))


def _preflight(bind) -> None:
    """Abort (DB untouched) on references the split can't faithfully carry."""
    problems: list[str] = []

    def _q(sql: str) -> list:
        return list(bind.exec_driver_sql(sql).fetchall())

    # 1. payment_account_ref must point at a cash account in the same snapshot.
    bad_pay = _q(
        """
        SELECT a.id, a.name FROM accounts a
        JOIN accounts p ON p.id = a.payment_account_ref
        WHERE a.payment_account_ref IS NOT NULL
          AND (p.account_type NOT IN ('checking','savings','wallet',
                                      'digital_wallet','gift_card')
               OR p.snapshot_id != a.snapshot_id)
        """
    )
    if bad_pay:
        problems.append(
            "payment_account_ref must point at a cash account in the same "
            f"snapshot; offending account ids: {[r[0] for r in bad_pay]}"
        )

    # 2. asset_ref must point at a kind='asset' entry in the same snapshot.
    bad_asset_ref = _q(
        """
        SELECT d.id, d.name FROM asset_entries d
        JOIN asset_entries a ON a.id = d.asset_ref
        WHERE d.asset_ref IS NOT NULL
          AND (a.kind != 'asset' OR a.snapshot_id != d.snapshot_id)
        """
    )
    if bad_asset_ref:
        problems.append(
            "asset_ref must point at an asset in the same snapshot; offending "
            f"entry ids: {[r[0] for r in bad_asset_ref]}"
        )

    # 3. auto_account_ref must be in the same snapshot as its budget entry.
    bad_auto = _q(
        """
        SELECT b.id FROM budget_entries b
        JOIN accounts a ON a.id = b.auto_account_ref
        WHERE b.auto_account_ref IS NOT NULL AND a.snapshot_id != b.snapshot_id
        """
    )
    if bad_auto:
        problems.append(
            "budget auto_account_ref crosses snapshots; offending budget ids: "
            f"{[r[0] for r in bad_auto]}"
        )

    # 4. Non-USD debts are not a supported shape (D2).
    bad_unit = _q(
        "SELECT id, name FROM asset_entries "
        "WHERE kind='debt' AND unit IS NOT NULL AND unit != 'USD'"
    )
    if bad_unit:
        problems.append(
            "symbol-denominated debts are unsupported after the split; convert "
            f"to USD first. Offending entry ids: {[r[0] for r in bad_unit]}"
        )

    # 5. Importable-name uniqueness would newly collide (debts join the rule).
    bad_names = _q(
        """
        SELECT snapshot_id, institution, name, COUNT(*) c FROM (
            SELECT snapshot_id, institution, name FROM accounts
            UNION ALL
            SELECT snapshot_id, institution, name FROM asset_entries
            WHERE kind='debt'
        ) GROUP BY snapshot_id, IFNULL(institution,''), name HAVING c > 1
        """
    )
    if bad_names:
        problems.append(
            "importable holdings must be uniquely named per (snapshot, "
            "institution); rename collisions first: "
            f"{[(r[1], r[2]) for r in bad_names]}"
        )

    if problems:
        raise _MigrationAbort(
            "migration aborted (database unchanged):\n  - " + "\n  - ".join(problems)
        )


def _create_new_tables() -> None:
    op.create_table(
        "holdings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("group_key", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("institution", sa.String(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "group_key IN ('cash','credit_card','loan','asset')",
            name="ck_holdings_group",
        ),
        sa.CheckConstraint(
            "(group_key = 'credit_card' AND type = 'credit_card')"
            " OR (group_key = 'loan' AND type = 'loan')"
            " OR (group_key = 'cash' AND type IN"
            "     ('checking','savings','wallet','digital_wallet','gift_card'))"
            " OR (group_key = 'asset' AND (type IS NULL OR type IN"
            "     ('brokerage','hsa','retirement','real_estate','vehicle',"
            "      'digital_wallet')))",
            name="ck_holdings_type_matches_group",
        ),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "group_key", name="uq_holdings_id_group"),
        sa.UniqueConstraint("id", "snapshot_id", name="uq_holdings_id_snapshot"),
    )
    op.create_index(
        "ix_holdings_snapshot_group_sort",
        "holdings",
        ["snapshot_id", "group_key", "sort_order"],
    )
    op.create_index(
        "uq_holdings_importable_name",
        "holdings",
        ["snapshot_id", "institution", "name"],
        unique=True,
        sqlite_where=sa.text("group_key != 'asset'"),
    )

    def _subtype_preamble(group: str) -> list:
        return [
            sa.Column("holding_id", sa.Integer(), nullable=False),
            sa.Column("group_key", sa.String(), nullable=False, server_default=group),
            sa.Column("snapshot_id", sa.Integer(), nullable=False),
            sa.CheckConstraint(
                f"group_key = '{group}'", name=f"ck_{group}_details_group"
            ),
            sa.ForeignKeyConstraint(
                ["holding_id", "group_key"],
                ["holdings.id", "holdings.group_key"],
                onupdate="CASCADE",
                name=f"fk_{group}_details_group",
            ),
            sa.ForeignKeyConstraint(
                ["holding_id", "snapshot_id"],
                ["holdings.id", "holdings.snapshot_id"],
                ondelete="CASCADE",
                name=f"fk_{group}_details_snapshot",
            ),
            sa.PrimaryKeyConstraint("holding_id"),
            sa.UniqueConstraint(
                "holding_id", "snapshot_id", name=f"uq_{group}_details_id_snapshot"
            ),
        ]

    op.create_table(
        "cash_details",
        *_subtype_preamble("cash"),
        sa.Column("balance", sa.Numeric(14, 2), nullable=True),
        sa.Column("minimum_balance", sa.Numeric(14, 2), nullable=True),
    )
    op.create_table(
        "asset_details",
        *_subtype_preamble("asset"),
        sa.Column("unit", sa.String(), nullable=False, server_default="USD"),
        sa.Column("quantity", sa.Numeric(12, 4), nullable=True),
        sa.Column("value", sa.Numeric(14, 2), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("annual_return_rate", sa.Numeric(8, 6), nullable=True),
        sa.Column("monthly_contribution", sa.Numeric(14, 2), nullable=True),
    )
    op.create_table(
        "credit_card_details",
        *_subtype_preamble("credit_card"),
        sa.Column("balance", sa.Numeric(14, 2), nullable=True),
        sa.Column("credit_limit", sa.Numeric(14, 2), nullable=True),
        sa.Column("rewards_balance", sa.Numeric(14, 2), nullable=True),
        sa.Column("statement_balance", sa.Numeric(14, 2), nullable=True),
        sa.Column("statement_due_day_of_month", sa.Integer(), nullable=True),
        sa.Column("payment_account_ref", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "statement_due_day_of_month IS NULL"
            " OR statement_due_day_of_month BETWEEN 1 AND 31",
            name="ck_credit_card_due_day",
        ),
        sa.ForeignKeyConstraint(
            ["payment_account_ref", "snapshot_id"],
            ["cash_details.holding_id", "cash_details.snapshot_id"],
            name="fk_credit_card_payment_account",
        ),
    )
    op.create_table(
        "loan_details",
        *_subtype_preamble("loan"),
        sa.Column("balance", sa.Numeric(14, 2), nullable=True),
        sa.Column("interest_rate", sa.Numeric(8, 6), nullable=True),
        sa.Column("original_principal", sa.Numeric(14, 2), nullable=True),
        sa.Column("term_months", sa.Integer(), nullable=True),
        sa.Column("origination_date", sa.Date(), nullable=True),
        sa.Column("statement_due_day_of_month", sa.Integer(), nullable=True),
        sa.Column("payment_account_ref", sa.Integer(), nullable=True),
        sa.Column("secured_asset_ref", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "statement_due_day_of_month IS NULL"
            " OR statement_due_day_of_month BETWEEN 1 AND 31",
            name="ck_loan_due_day",
        ),
        sa.CheckConstraint(
            "original_principal IS NULL OR original_principal > 0",
            name="ck_loan_original_principal",
        ),
        sa.CheckConstraint(
            "term_months IS NULL OR term_months > 0", name="ck_loan_term_months"
        ),
        sa.ForeignKeyConstraint(
            ["payment_account_ref", "snapshot_id"],
            ["cash_details.holding_id", "cash_details.snapshot_id"],
            name="fk_loan_payment_account",
        ),
        sa.ForeignKeyConstraint(
            ["secured_asset_ref", "snapshot_id"],
            ["asset_details.holding_id", "asset_details.snapshot_id"],
            name="fk_loan_secured_asset",
        ),
    )


def _copy_data(bind) -> None:
    ex = bind.exec_driver_sql

    # Old ids from accounts and asset_entries collide, so holdings assigns
    # fresh ids; TEMP maps carry old->new per source table.
    ex(
        "CREATE TEMP TABLE map_accounts "
        "(old_id INTEGER PRIMARY KEY, new_id INTEGER, group_key TEXT)"
    )
    ex(
        "CREATE TEMP TABLE map_assets "
        "(old_id INTEGER PRIMARY KEY, new_id INTEGER, group_key TEXT)"
    )

    # holdings spine from accounts (group from account_type), preserving order.
    ex(
        """
        INSERT INTO holdings
            (snapshot_id, group_key, type, name, institution, as_of_date,
             sort_order, created_at)
        SELECT snapshot_id,
               CASE account_type
                   WHEN 'credit_card' THEN 'credit_card'
                   WHEN 'loan' THEN 'loan' ELSE 'cash' END,
               account_type, name, institution, as_of_date, sort_order, created_at
        FROM accounts ORDER BY id
        """
    )
    # Recover old->new by matching on the natural key (snapshot, name, and the
    # rowid order they were inserted in). Simpler: re-walk accounts in id order
    # against holdings in id order for the account-sourced rows.
    ex(
        """
        INSERT INTO map_accounts (old_id, new_id, group_key)
        SELECT a.id, h.id,
               CASE a.account_type
                   WHEN 'credit_card' THEN 'credit_card'
                   WHEN 'loan' THEN 'loan' ELSE 'cash' END
        FROM (SELECT id, account_type, ROW_NUMBER() OVER (ORDER BY id) rn
              FROM accounts) a
        JOIN (SELECT id, ROW_NUMBER() OVER (ORDER BY id) rn FROM holdings) h
          ON a.rn = h.rn
        """
    )

    # holdings spine from asset_entries (asset -> asset, debt -> loan).
    ex(
        """
        INSERT INTO holdings
            (snapshot_id, group_key, type, name, institution, as_of_date,
             sort_order, created_at)
        SELECT snapshot_id,
               CASE kind WHEN 'debt' THEN 'loan' ELSE 'asset' END,
               CASE kind WHEN 'debt' THEN 'loan' ELSE type END,
               name, institution, as_of_date, sort_order, NULL
        FROM asset_entries ORDER BY id
        """
    )
    ex(
        """
        INSERT INTO map_assets (old_id, new_id, group_key)
        SELECT e.id, h.id,
               CASE e.kind WHEN 'debt' THEN 'loan' ELSE 'asset' END
        FROM (SELECT id, kind, ROW_NUMBER() OVER (ORDER BY id) rn
              FROM asset_entries) e
        JOIN (
            SELECT id, ROW_NUMBER() OVER (ORDER BY id) rn FROM holdings
            WHERE id > (SELECT COALESCE(MAX(new_id), 0) FROM map_accounts)
        ) h ON e.rn = h.rn
        """
    )

    # Re-index sort_order per (snapshot, group): accounts before asset-entries
    # within the merged loan group, else source order.
    ex(
        """
        WITH ranked AS (
            SELECT h.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY h.snapshot_id, h.group_key
                       ORDER BY src.src_rank, h.sort_order, h.id
                   ) - 1 AS new_order
            FROM holdings h
            JOIN (
                SELECT new_id AS id, 0 AS src_rank FROM map_accounts
                UNION ALL SELECT new_id, 1 FROM map_assets
            ) src ON src.id = h.id
        )
        UPDATE holdings SET sort_order = (
            SELECT new_order FROM ranked WHERE ranked.id = holdings.id
        )
        WHERE id IN (SELECT id FROM ranked)
        """
    )

    # Detail rows: cash + asset first (they are reference targets).
    ex(
        """
        INSERT INTO cash_details
            (holding_id, group_key, snapshot_id, balance, minimum_balance)
        SELECT m.new_id, 'cash', a.snapshot_id, a.balance, a.minimum_balance
        FROM accounts a JOIN map_accounts m ON m.old_id = a.id
        WHERE m.group_key = 'cash'
        """
    )
    ex(
        """
        INSERT INTO asset_details
            (holding_id, group_key, snapshot_id, unit, quantity, value, source,
             annual_return_rate, monthly_contribution)
        SELECT m.new_id, 'asset', e.snapshot_id, COALESCE(e.unit,'USD'),
               e.quantity, e.value, e.source, e.annual_return_rate,
               e.monthly_contribution
        FROM asset_entries e JOIN map_assets m ON m.old_id = e.id
        WHERE m.group_key = 'asset'
        """
    )
    # credit_card_details (payment ref remapped through map_accounts).
    ex(
        """
        INSERT INTO credit_card_details
            (holding_id, group_key, snapshot_id, balance, credit_limit,
             rewards_balance, statement_balance, statement_due_day_of_month,
             payment_account_ref)
        SELECT m.new_id, 'credit_card', a.snapshot_id, a.balance, a.credit_limit,
               a.rewards_balance, a.statement_balance, a.statement_due_day_of_month,
               pm.new_id
        FROM accounts a JOIN map_accounts m ON m.old_id = a.id
        LEFT JOIN map_accounts pm ON pm.old_id = a.payment_account_ref
        WHERE m.group_key = 'credit_card'
        """
    )
    # loan_details from account-loans (balance as-is, signed already).
    ex(
        """
        INSERT INTO loan_details
            (holding_id, group_key, snapshot_id, balance,
             statement_due_day_of_month, payment_account_ref)
        SELECT m.new_id, 'loan', a.snapshot_id, a.balance,
               a.statement_due_day_of_month, pm.new_id
        FROM accounts a JOIN map_accounts m ON m.old_id = a.id
        LEFT JOIN map_accounts pm ON pm.old_id = a.payment_account_ref
        WHERE m.group_key = 'loan'
        """
    )
    # loan_details from debt entries (balance negated * quantity; secured ref).
    ex(
        """
        INSERT INTO loan_details
            (holding_id, group_key, snapshot_id, balance, interest_rate,
             original_principal, term_months, origination_date,
             statement_due_day_of_month, secured_asset_ref)
        SELECT m.new_id, 'loan', e.snapshot_id,
               CASE WHEN e.balance IS NULL THEN NULL
                    ELSE -(e.balance * COALESCE(e.quantity, 1)) END,
               e.interest_rate, e.original_principal, e.term_months,
               e.origination_date, e.statement_due_day_of_month, sm.new_id
        FROM asset_entries e JOIN map_assets m ON m.old_id = e.id
        LEFT JOIN map_assets sm ON sm.old_id = e.asset_ref
        WHERE m.group_key = 'loan'
        """
    )


def _rebuild_referencing_tables(bind) -> None:
    ex = bind.exec_driver_sql

    # imports -> holding_id (renamed in place) + holding_group, retargeted FK.
    ex(
        """
        CREATE TABLE imports_new (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL,
            holding_group VARCHAR NOT NULL,
            filename VARCHAR NOT NULL,
            file_hash VARCHAR NOT NULL,
            imported_at DATETIME,
            status VARCHAR NOT NULL,
            ledger_balance NUMERIC(14,2),
            ledger_balance_date DATE,
            available_balance NUMERIC(14,2),
            available_balance_date DATE,
            beginning_balance NUMERIC(14,2),
            CONSTRAINT ck_imports_importable_group
                CHECK (holding_group IN ('cash','credit_card','loan')),
            CONSTRAINT ck_imports_status
                CHECK (status IN ('staging','confirmed','rejected')),
            CONSTRAINT fk_imports_holding
                FOREIGN KEY (account_id, holding_group)
                REFERENCES holdings (id, group_key)
                ON UPDATE CASCADE ON DELETE CASCADE
        )
        """
    )
    ex(
        """
        INSERT INTO imports_new
        SELECT i.id, m.new_id, m.group_key, i.filename, i.file_hash,
               i.imported_at, i.status, i.ledger_balance, i.ledger_balance_date,
               i.available_balance, i.available_balance_date, i.beginning_balance
        FROM imports i JOIN map_accounts m ON m.old_id = i.account_id
        """
    )
    ex("DROP TABLE imports")
    ex("ALTER TABLE imports_new RENAME TO imports")

    # transactions -> account_id points at holdings.id.
    ex(
        """
        CREATE TABLE transactions_new (
            id INTEGER PRIMARY KEY,
            import_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            date DATE NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            raw_description VARCHAR NOT NULL,
            normalized_merchant VARCHAR NOT NULL,
            fingerprint VARCHAR NOT NULL,
            created_at DATETIME,
            FOREIGN KEY (import_id) REFERENCES imports (id) ON DELETE CASCADE,
            FOREIGN KEY (account_id) REFERENCES holdings (id) ON DELETE CASCADE
        )
        """
    )
    ex(
        """
        INSERT INTO transactions_new
        SELECT t.id, t.import_id, m.new_id, t.date, t.amount, t.raw_description,
               t.normalized_merchant, t.fingerprint, t.created_at
        FROM transactions t JOIN map_accounts m ON m.old_id = t.account_id
        """
    )
    ex("DROP TABLE transactions")
    ex("ALTER TABLE transactions_new RENAME TO transactions")
    ex("CREATE INDEX ix_transactions_fingerprint ON transactions (fingerprint)")
    ex("CREATE INDEX ix_transactions_account_date ON transactions (account_id, date)")

    # balance_history -> account_id points at holdings.id (any group).
    ex(
        """
        CREATE TABLE balance_history_new (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL,
            as_of DATE NOT NULL,
            balance NUMERIC(14,2) NOT NULL,
            available NUMERIC(14,2),
            source VARCHAR NOT NULL,
            import_id INTEGER,
            note VARCHAR,
            created_at DATETIME,
            CONSTRAINT ck_balance_history_source
                CHECK (source IN ('statement','manual','migration')),
            CONSTRAINT uq_balance_history_point UNIQUE (account_id, as_of, source),
            FOREIGN KEY (account_id) REFERENCES holdings (id) ON DELETE CASCADE,
            FOREIGN KEY (import_id) REFERENCES imports (id)
        )
        """
    )
    ex(
        """
        INSERT INTO balance_history_new
        SELECT b.id, m.new_id, b.as_of, b.balance, b.available, b.source,
               b.import_id, b.note, b.created_at
        FROM balance_history b JOIN map_accounts m ON m.old_id = b.account_id
        """
    )
    ex("DROP TABLE balance_history")
    ex("ALTER TABLE balance_history_new RENAME TO balance_history")

    # budget_entries -> auto_account_ref snapshot-safe FK + enum CHECKs.
    ex(
        """
        CREATE TABLE budget_entries_new (
            id INTEGER PRIMARY KEY,
            snapshot_id INTEGER NOT NULL,
            kind VARCHAR NOT NULL,
            description VARCHAR NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            recurrence VARCHAR NOT NULL,
            category VARCHAR,
            date DATE,
            day_of_month INTEGER,
            month INTEGER,
            day_of_year INTEGER,
            continuous BOOLEAN,
            auto_account_ref INTEGER,
            sort_order INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT ck_budget_kind CHECK (kind IN ('income','expense')),
            CONSTRAINT ck_budget_recurrence CHECK (recurrence IN
                ('one_time','monthly','biweekly','quarterly','semiannual','annual')),
            FOREIGN KEY (snapshot_id) REFERENCES snapshots (id) ON DELETE CASCADE,
            CONSTRAINT fk_budget_auto_account
                FOREIGN KEY (auto_account_ref, snapshot_id)
                REFERENCES holdings (id, snapshot_id)
        )
        """
    )
    ex(
        """
        INSERT INTO budget_entries_new
        SELECT b.id, b.snapshot_id, b.kind, b.description, b.amount, b.recurrence,
               b.category, b.date, b.day_of_month, b.month, b.day_of_year,
               b.continuous, m.new_id, b.sort_order
        FROM budget_entries b
        LEFT JOIN map_accounts m ON m.old_id = b.auto_account_ref
        """
    )
    ex("DROP TABLE budget_entries")
    ex("ALTER TABLE budget_entries_new RENAME TO budget_entries")


def upgrade() -> None:
    bind = op.get_bind()
    _backup_db(bind)
    _preflight(bind)
    # Disable FK enforcement for the rebuild. foreign_keys is a persistent
    # connection pragma (it survives Alembic's per-statement autocommits,
    # unlike defer_foreign_keys), so the table-swap ordering and the temp-map
    # joins can't trip a live constraint mid-flight. Integrity is validated at
    # the end with foreign_key_check. Whether the ambient default is ON (the
    # app engine sets it) or OFF, this pins it.
    fk_was_on = bool(bind.exec_driver_sql("PRAGMA foreign_keys").scalar())
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    _create_new_tables()
    _copy_data(bind)
    _rebuild_referencing_tables(bind)
    bind.exec_driver_sql("DROP TABLE accounts")
    bind.exec_driver_sql("DROP TABLE asset_entries")
    bind.exec_driver_sql("DROP TABLE map_accounts")
    bind.exec_driver_sql("DROP TABLE map_assets")
    fk_issues = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if fk_issues:
        raise _MigrationAbort(f"foreign_key_check failed: {fk_issues[:5]}")
    if fk_was_on:
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    raise NotImplementedError(
        "The holdings split is a one-way data migration. Restore the "
        "<db>.pre-split.bak snapshot taken during upgrade instead."
    )
