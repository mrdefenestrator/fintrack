# Holdings schema split — supertype + four subtype tables

**Status:** draft · drafted 2026-08-24

## Goal

Replace the two wide variant-record tables — `accounts` (cash + credit cards +
account-loans behind `account_type`) and `asset_entries` (assets + debts behind
`kind`) — with a slim `holdings` supertype table plus four subtype tables that
match the Holdings sheet's groups exactly:

    holdings  (shared spine: snapshot, group, type, institution, name, as_of, order)
      ├── cash_details
      ├── credit_card_details
      ├── loan_details          ← unifies account-loans and debt-entry loans
      └── asset_details

Everything downstream that references "an account" (`imports`, `transactions`,
`balance_history`, `budget_entries.auto_account_ref`, a credit card's payment
account, a loan's secured asset) keeps a **single real foreign-key target** —
the supertype — which is why this shape wins over four fully independent
tables: cash, credit cards, *and* loans are all statement-importable, and four
independent tables would force either parallel nullable FK columns or an
unenforced `(table, id)` pair on the ledger tables.

Problems this fixes:

1. **Sparse variant columns with no DB enforcement.** Today nothing stops a
   checking account with a `credit_limit` or an asset row with an
   `interest_rate`. Each subtype table carries only its group's columns.
2. **Loans live in two places** (`account_type='loan'` rows and `kind='debt'`
   rows) with different capabilities, special-cased throughout
   `web/routes/holdings.py` (`_account_group_key`, the loan branch of
   `_account_col_fields`, the mixed-group non-reorderable rule).
   `docs/notes-loan-origination.md` already declared the account-loan rows
   would "disappear when the unified tables are split" — this is that split.
3. **Untyped, snapshot-unsafe references.** `payment_account_ref` can point at
   another credit card, `asset_ref` at a debt row, and either at a row in a
   *different snapshot*; only application code (and the snapshot-copy
   remapping) keeps this straight. Composite FKs make all three wrong states
   unrepresentable.
4. **Derived data stored.** `accounts.available` duplicates
   `credit_limit + balance` and needs two repository functions to keep it
   honest. It is dropped; Available is computed (the Holdings sheet already
   treats it as computed/read-only).
5. **Mixed money precision** (`Numeric(12,2)` vs `(14,2)`) — unified at
   `(14,2)`.

Out of scope here (kept as-is): `budget_entries`' recurrence sparsity (mild,
one consumer), `merchant_cache`/`categories`/`price_cache`, the classifier.

## The shared spine

`holdings` carries exactly the columns the Holdings sheet shares across all
four groups — Institution · Type · Name · (Amount lives per-subtype) — plus
scoping and ordering:

| column        | notes                                                        |
| ------------- | ------------------------------------------------------------ |
| `id`          | the one id every other table references                      |
| `snapshot_id` | FK → snapshots, CASCADE (unchanged convention)               |
| `group_key`   | `cash` \| `credit_card` \| `loan` \| `asset` (CHECK)          |
| `type`        | holding-type vocabulary; CHECK-constrained *per group*       |
| `name`        | NOT NULL                                                     |
| `institution` | nullable                                                     |
| `as_of_date`  | shared As-Of column                                          |
| `sort_order`  | scoped per `(snapshot_id, group_key)` — see "Reorder" below  |
| `created_at`  |                                                              |

`group_key` vs `type` is deliberately redundant for credit cards and loans
(`group_key='credit_card' ⇔ type='credit_card'`, enforced by CHECK): keeping
`type` on the spine keeps the tier map in `fintrack/core/types.py`, the Type
column/filter, and `tiered_totals` working off one column, while `group_key`
is the subtype discriminator the FKs hang off.

Two unique constraints exist purely as **composite-FK targets**:

- `UNIQUE (id, group_key)` — lets a child row *prove* what group its parent
  is in (subtype rows, `imports`).
- `UNIQUE (id, snapshot_id)` — lets a reference *prove* it stays inside its
  own snapshot (`budget_entries.auto_account_ref`, and transitively every
  ref that goes through a detail table).

Plus one partial unique index preserving today's account-identity rule for
statement import matching, without imposing it on assets (which never had it):

```sql
CREATE UNIQUE INDEX uq_holdings_importable_name
    ON holdings (snapshot_id, institution, name)
    WHERE group_key != 'asset';
```

This constraint is load-bearing application behavior, not just a backstop:
`web/routes/imports.py:create_account` catches the `IntegrityError` from
today's `uq_accounts_snapshot_institution_name` to surface "an account named …
already exists". The partial index keeps that catch working (OFX *matching*
itself is a heuristic in-memory scorer in `_match_account` and doesn't query
by the constraint). Note the scope widens slightly: loans that were debt
entries had no uniqueness before — the migration pre-flight detects collisions
(step 0.5).

## Proposed DDL (`fintrack/core/models.py` replacement section)

```python
GROUP_KEYS = ("cash", "credit_card", "loan", "asset")

# Per-group type vocabularies; must stay in lockstep with core.types.
_CASH_TYPES = ("checking", "savings", "wallet", "digital_wallet", "gift_card")
_ASSET_TYPES = ("brokerage", "hsa", "retirement", "real_estate", "vehicle",
                "digital_wallet")

holdings = Table(
    "holdings",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "snapshot_id",
        Integer,
        ForeignKey("snapshots.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("group_key", String, nullable=False),
    # Holding type (core.types.HOLDING_TYPE_TIER). Only the asset group may
    # leave it NULL (unclassified -> DEFAULT_TIER); for credit_card/loan it is
    # pinned to the group.
    Column("type", String, nullable=True),
    Column("name", String, nullable=False),
    Column("institution", String, nullable=True),
    Column("as_of_date", Date, nullable=True),
    Column("sort_order", Integer, nullable=False, default=0),
    Column("created_at", DateTime, default=_utcnow),
    CheckConstraint(
        "group_key IN ('cash','credit_card','loan','asset')",
        name="ck_holdings_group",
    ),
    CheckConstraint(
        "(group_key = 'credit_card' AND type = 'credit_card')"
        " OR (group_key = 'loan' AND type = 'loan')"
        " OR (group_key = 'cash' AND type IN"
        "     ('checking','savings','wallet','digital_wallet','gift_card'))"
        " OR (group_key = 'asset' AND (type IS NULL OR type IN"
        "     ('brokerage','hsa','retirement','real_estate','vehicle',"
        "      'digital_wallet')))",
        name="ck_holdings_type_matches_group",
    ),
    # Composite-FK targets (see notes above).
    UniqueConstraint("id", "group_key", name="uq_holdings_id_group"),
    UniqueConstraint("id", "snapshot_id", name="uq_holdings_id_snapshot"),
    Index("ix_holdings_snapshot_group_sort",
          "snapshot_id", "group_key", "sort_order"),
)

# Import matching identity for importable holdings only (assets exempt).
Index(
    "uq_holdings_importable_name",
    holdings.c.snapshot_id, holdings.c.institution, holdings.c.name,
    unique=True,
    sqlite_where=holdings.c.group_key != "asset",
)


def _subtype_columns(group: str) -> list:
    """The three-column preamble every subtype table shares.

    holding_id is the PK and, together with the denormalized group_key and
    snapshot_id copies, forms the two composite FKs that make the subtype
    link airtight:
      (holding_id, group_key)   -> holdings(id, group_key)   [right table]
      (holding_id, snapshot_id) -> holdings(id, snapshot_id) [right snapshot,
                                                              CASCADE cleanup]
    ON UPDATE CASCADE on the group FK means a group change on the parent is
    rejected (the cascaded group_key would violate this table's CHECK) unless
    the subtype row is moved first — exactly the transition discipline the
    repositories implement (delete old detail -> update parent -> insert new).
    """
    return [
        Column("holding_id", Integer, primary_key=True),
        Column("group_key", String, nullable=False, server_default=group),
        Column("snapshot_id", Integer, nullable=False),
        CheckConstraint(f"group_key = '{group}'", name=f"ck_{group}_group"),
        ForeignKeyConstraint(
            ["holding_id", "group_key"],
            ["holdings.id", "holdings.group_key"],
            onupdate="CASCADE",
            name=f"fk_{group}_details_group",
        ),
        ForeignKeyConstraint(
            ["holding_id", "snapshot_id"],
            ["holdings.id", "holdings.snapshot_id"],
            ondelete="CASCADE",
            name=f"fk_{group}_details_snapshot",
        ),
        # Composite-FK target for typed, same-snapshot refs INTO this group.
        UniqueConstraint("holding_id", "snapshot_id",
                         name=f"uq_{group}_details_id_snapshot"),
    ]


cash_details = Table(
    "cash_details",
    metadata,
    *_subtype_columns("cash"),
    # Canonical signed balance; denormalized cache of the latest
    # balance_history point — write through record_balance(), as today.
    Column("balance", Numeric(14, 2), nullable=True),
    Column("minimum_balance", Numeric(14, 2), nullable=True),  # reserve target
)

credit_card_details = Table(
    "credit_card_details",
    metadata,
    *_subtype_columns("credit_card"),
    Column("balance", Numeric(14, 2), nullable=True),  # signed; negative = owed
    Column("credit_limit", Numeric(14, 2), nullable=True),
    # NOTE: no `available` column. Available = credit_limit + balance is
    # computed everywhere (already read-only on the Holdings sheet).
    Column("rewards_balance", Numeric(14, 2), nullable=True),
    Column("statement_balance", Numeric(14, 2), nullable=True),
    Column("statement_due_day_of_month", Integer, nullable=True),
    Column("payment_account_ref", Integer, nullable=True),
    CheckConstraint(
        "statement_due_day_of_month IS NULL"
        " OR statement_due_day_of_month BETWEEN 1 AND 31",
        name="ck_cc_due_day",
    ),
    # Typed + same-snapshot: a card's payment account must be a CASH holding
    # in the SAME snapshot. (NO ACTION, matching today's ownership-ref rule:
    # direct deletes of a referenced row are blocked; snapshot-level cascade
    # still cleans up.)
    ForeignKeyConstraint(
        ["payment_account_ref", "snapshot_id"],
        ["cash_details.holding_id", "cash_details.snapshot_id"],
        name="fk_cc_payment_account",
    ),
)

loan_details = Table(
    "loan_details",
    metadata,
    *_subtype_columns("loan"),
    # Signed like every other balance (negative = owed): consistent with
    # balance_history and statement imports. The repositories expose the
    # debt-side dict API positive (amount owed), negating at the boundary.
    Column("balance", Numeric(14, 2), nullable=True),
    Column("interest_rate", Numeric(8, 6), nullable=True),
    Column("original_principal", Numeric(14, 2), nullable=True),
    Column("term_months", Integer, nullable=True),
    Column("origination_date", Date, nullable=True),
    Column("statement_due_day_of_month", Integer, nullable=True),
    # Account-loans brought their Linked payment account; debt-loans brought
    # their secured asset. The unified loan may have either or both.
    Column("payment_account_ref", Integer, nullable=True),
    Column("secured_asset_ref", Integer, nullable=True),
    CheckConstraint(
        "statement_due_day_of_month IS NULL"
        " OR statement_due_day_of_month BETWEEN 1 AND 31",
        name="ck_loan_due_day",
    ),
    CheckConstraint(
        "original_principal IS NULL OR original_principal > 0",
        name="ck_loan_original_principal",
    ),
    CheckConstraint(
        "term_months IS NULL OR term_months > 0",
        name="ck_loan_term_months",
    ),
    ForeignKeyConstraint(
        ["payment_account_ref", "snapshot_id"],
        ["cash_details.holding_id", "cash_details.snapshot_id"],
        name="fk_loan_payment_account",
    ),
    ForeignKeyConstraint(
        ["secured_asset_ref", "snapshot_id"],
        ["asset_details.holding_id", "asset_details.snapshot_id"],
        name="fk_loan_secured_asset",
    ),
)

asset_details = Table(
    "asset_details",
    metadata,
    *_subtype_columns("asset"),
    # Denomination of quantity: "USD" (price 1, amount == value) or a
    # ticker/symbol whose per-unit price comes from price_cache.
    Column("unit", String, nullable=False, server_default="USD"),
    Column("quantity", Numeric(12, 4), nullable=True),  # default-1 semantics
    Column("value", Numeric(14, 2), nullable=True),  # per-unit value (manual)
    Column("source", String, nullable=True),  # valuation source
    Column("annual_return_rate", Numeric(8, 6), nullable=True),
    Column("monthly_contribution", Numeric(14, 2), nullable=True),
)
```

### Retargeted references on the existing tables

All rebuilt in the same migration (SQLite table rebuild — see plan):

- **`imports`**: `account_id` → `holding_id` (FK → `holdings.id`, CASCADE) plus
  a denormalized `holding_group` column enforcing importability at the DB:

  ```python
  Column("holding_id", Integer, nullable=False),
  Column("holding_group", String, nullable=False),
  CheckConstraint(
      "holding_group IN ('cash','credit_card','loan')",
      name="ck_imports_importable_group",
  ),
  ForeignKeyConstraint(
      ["holding_id", "holding_group"],
      ["holdings.id", "holdings.group_key"],
      onupdate="CASCADE", ondelete="CASCADE",
      name="fk_imports_holding",
  ),
  ```

  ON UPDATE CASCADE + the CHECK means a holding with import history cannot be
  retyped into the asset group — the DB rejects it, which is correct.
- **`transactions`**: `account_id` → `holding_id`, plain FK → `holdings.id`
  CASCADE (rows only ever arrive via an import, which is already constrained).
- **`balance_history`**: `account_id` → `holding_id`, plain FK → `holdings.id`
  CASCADE. Deliberately *not* restricted to importable groups: this opens
  balance/value history for assets later (net-worth projections want it).
- **`budget_entries`**: `auto_account_ref` becomes snapshot-safe via
  `FOREIGN KEY (auto_account_ref, snapshot_id) REFERENCES holdings (id,
  snapshot_id)` (untyped on purpose — an income deposits to cash, a CC-paid
  expense may reference the card). While the table is being rebuilt anyway,
  add the cheap enum CHECKs: `kind IN ('income','expense')`, `recurrence IN
  ('one_time','monthly','biweekly','quarterly','semiannual','annual')`.
- Same ride-along CHECKs on rebuilt tables: `imports.status IN
  ('staging','confirmed','rejected')`, `balance_history.source IN
  ('statement','manual','migration')`.

### Old → new column map

| old                                          | new                                            |
| -------------------------------------------- | ---------------------------------------------- |
| `accounts.account_type`                      | `holdings.type` (+ derived `group_key`)        |
| `accounts.name/institution/as_of_date/sort_order` | `holdings.*`                              |
| `accounts.balance` (cash row)                | `cash_details.balance`                         |
| `accounts.minimum_balance`                   | `cash_details.minimum_balance`                 |
| `accounts.balance/credit_limit/rewards_balance/statement_balance/statement_due_day_of_month/payment_account_ref` (CC row) | `credit_card_details.*` |
| `accounts.available`                         | **dropped** (computed)                         |
| `accounts` row with `account_type='loan'`    | `holdings(group_key='loan')` + `loan_details` (origination fields NULL) |
| `asset_entries` row, `kind='asset'`          | `holdings(group_key='asset')` + `asset_details` |
| `asset_entries` row, `kind='debt'`           | `holdings(group_key='loan')` + `loan_details`  |
| `asset_entries.balance` (debt, positive owed) | `loan_details.balance` = `-(balance × COALESCE(quantity,1))` (signed) |
| `asset_entries.asset_ref`                    | `loan_details.secured_asset_ref`               |
| `asset_entries.unit/quantity` (debt rows)    | **folded/dropped** — see decision D2           |
| `imports.account_id` etc.                    | `imports.holding_id` (+ `holding_group`)       |

## Alembic migration plan

One new revision on head (`revises: f4a5b6c7d8e9`), data-copying, SQLite-only
(matching the project). **Irreversible: `downgrade()` raises.** The upgrade
copies the SQLite file to `<db>.pre-split.bak` alongside before touching
anything (cheap, and the only honest downgrade story for a personal-finance
DB).

Execution wrapper: run with `PRAGMA defer_foreign_keys=ON` for the
transaction, and finish with `PRAGMA foreign_key_check` — the standard SQLite
rebuild pattern, so insert ordering inside the migration can't silently
produce orphans.

### Step 0 — pre-flight checks (abort with a clear message, DB untouched)

1. `payment_account_ref` targets that are not cash-group accounts, or that
   live in a different snapshot.
2. `asset_ref` targets that are not `kind='asset'`, or cross-snapshot.
3. `auto_account_ref` cross-snapshot.
4. Debt rows with `unit != 'USD'` → **abort** (see D2); debt rows with
   `quantity NOT IN (NULL, 1)` are auto-folded (D2) — report them.
5. Name collisions that would break `uq_holdings_importable_name`: a
   `kind='debt'` entry whose `(snapshot, institution, name)` collides with an
   account, or with another debt. List them; abort (user renames first).
6. CC rows where `available != credit_limit + balance` (both non-NULL):
   **warn** (log the drift being discarded), don't abort — `balance` is
   canonical and `available` is being dropped.

### Step 1 — create the five new tables

`op.create_table` for `holdings`, `cash_details`, `asset_details`,
`credit_card_details`, `loan_details` (in that order: the ref targets first),
plus the partial unique index.

### Step 2 — copy with id remapping

Old ids from `accounts` and `asset_entries` collide, so `holdings` assigns
fresh ids. Two TEMP map tables keep the SQL declarative:

```sql
CREATE TEMP TABLE map_accounts (old_id INTEGER PRIMARY KEY,
                                new_id INTEGER NOT NULL,
                                group_key TEXT NOT NULL);
CREATE TEMP TABLE map_assets   (old_id INTEGER PRIMARY KEY,
                                new_id INTEGER NOT NULL,
                                group_key TEXT NOT NULL);
```

Copy order (parents before referencing rows):

1. `accounts` → `holdings` (group from `account_type`: `credit_card` →
   `credit_card`, `loan` → `loan`, else `cash`), recording `map_accounts`.
2. `asset_entries` → `holdings` (`kind='asset'` → `asset`, `kind='debt'` →
   `loan`), recording `map_assets`.
3. `holdings.sort_order` re-indexed per `(snapshot_id, group_key)` with
   `ROW_NUMBER() OVER (PARTITION BY snapshot_id, group_key ORDER BY
   <source-table rank>, <old sort_order>)` — accounts before asset-entries
   within the merged loan group, preserving today's on-screen order.
4. `cash_details`, `asset_details` from their sources + maps.
5. `credit_card_details` (payment refs via `map_accounts`).
6. `loan_details` — two INSERT..SELECTs:
   - from `accounts` loan rows: `balance` copied as-is (already signed),
     origination fields NULL, `payment_account_ref` via `map_accounts`;
   - from `asset_entries` debt rows: `balance = -(balance ×
     COALESCE(quantity, 1))`, origination fields copied,
     `secured_asset_ref = map_assets[asset_ref]`.

### Step 3 — rebuild the referencing tables

For each of `imports`, `transactions`, `balance_history`, `budget_entries`:
create `<table>_new` with the final DDL (renamed FK column, new composite FKs,
ride-along CHECKs), `INSERT .. SELECT .. JOIN map_accounts`, drop old, rename.
(`imports.holding_group` comes from the map's `group_key`.) A plain
`batch_alter_table` can't do the id remap, hence the explicit rebuild — it is
the same table-recreate SQLite performs under batch mode anyway, and follows
the repo's own fold-data-then-rebuild precedent
(`b0b3c9940bc5_drop_partial_account_number…`).

### Step 4 — drop `accounts` and `asset_entries`, drop the temp maps, run `PRAGMA foreign_key_check`.

## Repository / API plan

The dict API (`Account` / `AssetEntry` TypedDicts, `"limit"`, `"asOfDate"`,
`"assetRef"` field names) **does not change** in phase 1 — routes, templates,
`calculations.py`, `tables.py`, and the CLI keep working off dicts. What
changes behind the repository boundary:

- `fintrack/accounts/repository.py` → reads/writes `holdings ⋈ cash_details`
  and `holdings ⋈ credit_card_details`. `get_accounts` returns **cash +
  credit-card groups only** (see D3: account-loans move to the debt side).
  The `available` dict key is computed at read time; `_derive_cc_balance` /
  `_derive_cc_available` collapse into one derivation on read plus the
  existing balance-edit rule.
- `fintrack/networth/repository.py` → `get_asset_entries` returns the asset
  group as `kind='asset'` dicts and the loan group as `kind='debt'` dicts
  (negating `balance` to the positive owed convention the dict API and
  amortization helpers use).
- Group transitions (a Type edit that changes group, e.g. `savings` →
  `credit_card`): one transaction — delete old detail row, update
  `holdings.group_key`/`type`, insert new detail row carrying shared fields.
  The composite FKs make a mismatched or dangling detail row impossible; the
  DB rejects retyping a holding that has import history out of the importable
  groups.
- `fintrack/snapshots/repository.py` copy: one `holdings` copy loop + four
  detail copies; the ref remapping shrinks to remapping `holding_id`s (the
  composite FKs now *verify* what the code previously just had to get right).
- `fintrack/ledger/repository/accounts.py` (OFX matching) matches against
  `holdings` where `group_key IN ('cash','credit_card','loan')`, backed by
  `uq_holdings_importable_name`.
- `record_balance()` keeps writing the subtype `balance` cache + a
  `balance_history` row, addressed by `holding_id`.

Phase 2 (separate PRs, enabled by the split):

- Replace sort-order-index addressing in `networth/repository.py`
  (`update_asset_entry(index)`) with `holding_id` addressing — removes the
  reorder race and the `_db_id` back-channel.
- `web/routes/holdings.py` cleanup: `_account_group_key`/`_asset_group_key`
  become `holdings.group_key`; the loan branch of `_account_col_fields`
  disappears (one loan shape); per-group `sort_order` turns `reorder`'s
  local→global slot mapping into a straight permutation; every group is
  always reorderable (the mixed-group rule dies). Same for
  `web/routes/projections.py:_row_group_key`, which mirrors the account-loan
  special case.
- `core/ordering.py:reorder_by_positions` (generic over any table with
  `snapshot_id` + `sort_order`) gains an optional group filter so a
  reorder permutes only `(snapshot_id, group_key)` — budget keeps using it
  unchanged.
- CLI: `accounts` lists cash + credit cards; `debts` lists all loans
  (including former account-loans, now with origination fields available).
  `accounts add --type loan` goes away — `_ACCOUNT_TYPE_KEYS` in
  `core/types.py` drops `"loan"` (the group is now the discriminator, so the
  type vocabulary no longer needs to be dual-purpose), and `debts add` is the
  one way to create a loan.
- Consolidate the two parallel account CRUD surfaces:
  `fintrack/ledger/repository/accounts.py` (self-described "transitional",
  identity columns only, used by the import flow) folds into the holdings
  repository once both speak `holding_id`.
- Optional later: asset value points in `balance_history` (source `manual`),
  feeding projections.

## Decisions to confirm (flagged, with chosen defaults)

- **D1 — signed loan balances in the DB.** `loan_details.balance` is signed
  (negative = owed) like every other balance and like `balance_history`, so
  statement imports for loans need no special case. The debt dict API stays
  positive; the repository negates at the boundary. Alternative (positive in
  DB) would instead special-case the import/history path — worse.
- **D2 — debts lose `unit`/`quantity`.** This *is* a capability removal, not
  just cleanup: `calculations._entry_subtotal` today prices a debt exactly
  like an asset (`balance × quantity`, or `quantity × rates[unit]` for a
  symbol-denominated debt), and `equity_pairs`/`net_nonliquid_*` inherit
  that. But no entry path creates such debts — the CLI (`debts add`) and the
  Holdings sheet both treat debts as USD/qty-1, and the debt columns on the
  sheet don't expose unit/qty. Decision: a symbol-denominated or
  multi-quantity *liability* is not a shape this app supports; the migration
  folds USD quantities into the balance and aborts on non-USD debts (none
  expected). `_entry_subtotal`'s debt branch simplifies accordingly. Assets
  keep unit/quantity/value unchanged. If a real need appears later (e.g. a
  BTC-denominated loan), it would be a deliberate `loan_details` addition.
- **D3 — account-loans move from the Accounts API to the debt side.** They
  join the unified loan group, gaining origination/amortization fields.
  Visible change: CLI `accounts list` no longer shows them; `debts` does.
  Holdings page is unaffected (same four groups, same rows).
- **D4 — `available` is not stored.** Any drift between stored `available`
  and `credit_limit + balance` is logged by the migration and discarded in
  favor of `balance` (canonical, import-fed). This kills an invariant that is
  currently maintained in **three** independent places:
  `accounts/repository.py` (`_derive_cc_available`/`_derive_cc_balance`),
  `accounts/balance_history.py` (`_sync_account_to_latest` re-derives it on
  every history write), and `migrate/yaml_import.py` (inline copy of the
  derivation). After the split there is one derivation, at read time.
- **D5 — snapshot-cascade vs NO ACTION refs.** The ownership refs keep
  today's convention (block direct deletes, cleaned up by snapshot cascade).
  The snapshot-delete e2e/unit tests must stay green with the new FK
  topology; if SQLite's within-statement cascade ordering ever trips the NO
  ACTION refs, the fallback is clearing refs inside `delete_snapshot()`
  before the cascade — a repository-level mitigation, no schema change.

## Blast radius

From a full-codebase inventory (2026-08-24). The dict API is the firewall:
everything below the first group changes; everything in the second group
should not.

### Touches SQL directly — must change

| module | what it does today | port |
| --- | --- | --- |
| `fintrack/core/models.py` | table definitions | replaced per the DDL above |
| `fintrack/accounts/repository.py` | accounts CRUD + CC derivation + `record_balance` calls | `holdings ⋈ cash/credit_card_details`; derivation collapses (D4) |
| `fintrack/networth/repository.py` | asset_entries CRUD | `holdings ⋈ asset/loan_details`; balance sign at boundary (D1) |
| `fintrack/accounts/balance_history.py` | history upsert + syncs `accounts.balance`/`available`/`as_of_date` | sync targets the subtype `balance` + spine `as_of_date`; drops its copy of the CC derivation |
| `fintrack/snapshots/repository.py` | `copy_snapshot` copies rows + remaps 3 ref kinds | copy `holdings` then fan out per group; refs remap through one `holding_id` map (composite FKs now verify it) |
| `fintrack/ledger/repository/accounts.py` | parallel "transitional" CRUD (identity cols) used by import flow | point at `holdings` importable groups; candidate for consolidation (phase 2) |
| `fintrack/ledger/repository/imports.py` | joins `imports → accounts` for staging lists | join through `holdings`; `imports.holding_id`/`holding_group` |
| `fintrack/migrate/legacy.py` | raw `insert()` into both tables + `payment_account_ref`/`asset_ref` second pass + balance_history seeding | two-level inserts; ref passes target detail tables |
| `fintrack/migrate/yaml_import.py` | raw inserts into both tables, inline CC derivation, **bypasses balance_history** (pre-existing gap) | two-level inserts; drop the inline derivation; decide consciously whether to keep or fix the history bypass |
| `fintrack/core/ordering.py` | generic `(snapshot_id, sort_order)` permutation helper | + optional group filter (budget unchanged) |
| `web/routes/imports.py` | heuristic `_match_account` over dicts; `create_account` catches the unique-constraint `IntegrityError` | matcher unchanged (dicts); the catch keeps working via `uq_holdings_importable_name` |

### Dict-API only — should not change in phase 1

`fintrack/networth/calculations.py` (except `_entry_subtotal`'s debt branch,
D2), `amortization.py`, `prices.py`, `fintrack/core/{tables,loader,filters}.py`,
`fintrack/budget/*`, `fintrack/projections/{engine,estimators}.py`,
`fintrack/cli/*`, `web/routes/*` (holdings/projections get their phase-2
cleanups; `ofx.py` is pure parsing, no DB access at all).

### Tests

- Most fixtures already go through the repositories (`tests/finances/*`,
  `tests/projections/*`, `tests/spending/*` importer tests, all e2e) — they
  track the dict API and survive phase 1.
- Three raw `insert(accounts)` fixtures need the two-level insert:
  `tests/spending/test_repository/test_merchants.py`,
  `test_categories.py` (`_insert_dummy_transaction`),
  `tests/spending/test_web/test_merchants_routes.py`.
- `tests/migrate/test_legacy.py` asserts against `models.accounts` /
  `models.asset_entries` after a legacy migration — assertions move to
  `holdings` + details.
- `tests/finances/test_repository.py` has explicit loan-as-account coverage
  (`by_name["Mortgage"]["type"] == "loan"`) — becomes a loan-group test (D3).
- Unit fixtures use `metadata.create_all()` (models, not the migration
  chain), so the new tables are exercised immediately; the data-copy
  migration itself needs its own test with a populated old-schema DB
  (pattern: `tests/migrate/` builds source schemas by hand).
