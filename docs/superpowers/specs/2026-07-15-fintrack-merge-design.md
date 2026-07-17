# fintrack — Merging spending + finances — Design Spec

**Date:** 2026-07-15
**Status:** Approved

## Problem

Two sibling apps track different halves of the same financial picture:

- **spending** (port 5002, `SPENDING_DB`) — transaction ledger: OFX/CSV statement import with staging→confirm, fingerprint dedup, Claude Haiku merchant classification (privacy: only normalized merchant names are sent), corrections overlay, monthly trends. Recently gained balance capture (ledger/available/beginning balances stored on `imports` but not yet surfaced — its design spec deferred "auto-update finances balances" as a cross-project feature).
- **finances** (port 5001, `FINANCES_DB`) — snapshot-scoped (multi-household) net-worth and cash-flow tool: accounts with manually entered balances, scheduled budget entries with recurrence/proration, assets/debts, funding analysis, six key numbers, remainder-of-month projection. Tables prefixed `fin_`. Its DESIGN.md/CLAUDE.md predate the SQLite migration; code is ground truth.

Keeping them separate blocks the features that need both: automatic balance updates from statements, balance history over time, and multi-month financial projections that combine scheduled budget flows with observed spending patterns.

## Approach

Merge both codebases into a **new repository `fintrack`** (package `fintrack`, CLI entrypoint `fintrack.py`, env var `FINTRACK_DB`, web port **5003**, Docker image `fintrack`), preserving both git histories via `git merge --allow-unrelated-histories`. Consolidate incrementally over eight phases, each leaving the app working and CI green. Key decisions:

1. **Single accounts table** merging both roles: statement-import target (spending) and balance/limit/autopay/funding config (finances).
2. **Snapshots (households) scope everything** — accounts, imports, transactions, budget entries, and assets are all scoped to a snapshot. Classification knowledge (merchant cache, categories) stays global.
3. **Fresh Alembic baseline** in the new repo; existing data arrives via a one-time `migrate-legacy` command, never via upgrade of an old DB.
4. **Balance history** as a first-class table, fed by statement-captured balances and manual edits, with projections built on top.

## Target package layout

```
fintrack/
  fintrack.py                # Click CLI entry
  fintrack/
    core/        db.py (FK-pragma listener), models.py (single MetaData),
                 config.py (FINTRACK_DB + configs path resolution), types.py,
                 formatting.py, filters.py
    ledger/      importer/{ofx,csv_parser,normalize,dedup}.py, classifier.py,
                 repositories: imports, transactions, merchants, corrections,
                 categories, aggregations
    accounts/    repository.py (unified accounts), balance_history.py, matching.py
    budget/      repository.py, recurrence.py (extracted from finances/calculations.py)
    networth/    repository.py (assets/debts), calculations.py (key numbers, funding)
    projections/ engine.py, estimators.py
    snapshots/   repository.py
    migrate/     legacy.py (one-time importer), mapping.py
    cli/         one module per command group
  web/           app.py + routes/ + templates/ (single base.html) + static/
  migrations/ + alembic.ini   # fresh single chain, one baseline revision
  configs/categories.yaml, configs/normalization.yaml, configs/institutions/
  tests/ (unit, by domain) + tests/e2e/
  data/example.yaml
```

## Unified data model

Fresh single `MetaData` in `fintrack/core/models.py`; `fin_` prefixes dropped.

### snapshots

`id`, `name` (unique), `created_at`. Finances' semantics, now scoping the whole app.

### accounts (merged)

| Column | Notes |
|--------|-------|
| `id` | PK |
| `snapshot_id` | FK→snapshots, NOT NULL, ON DELETE CASCADE |
| `name` | unique per `(snapshot_id, name)` — replaces spending's global unique |
| `institution` | nullable |
| `account_type` | `checking\|savings\|gift_card\|wallet\|digital_wallet\|credit_card\|loan\|other` |
| `balance` | Numeric(12,2) — **canonical signed balance for all types** (negative = owed on CCs) |
| `credit_limit` | renamed from finances' `limit` (SQL keyword) |
| `available` | editing it or `credit_limit` on a CC recomputes `balance = available − credit_limit` |
| `rewards_balance`, `statement_balance`, `statement_due_day_of_month` | from finances |
| `payment_account_ref` | self-FK (NO ACTION), CC autopay source |
| `minimum_balance` | from finances |
| `as_of_date` | **Date** (finances stores strings — converted during migration) |
| `partial_account_number` | also powers spending's OFX account detection |
| `sort_order` | NOT NULL DEFAULT 0 |
| `created_at` | |

The canonical-signed-balance convention exists because `balance_history` needs one semantics across sources: OFX gives a signed ledger balance directly; finances' UI gives available/limit for CCs. `_credit_card_balance_owed()` generalizes to prefer `balance` with available−limit fallback.

### imports / transactions

As spending today (imports keep the balance-capture columns; staging→confirmed→rejected lifecycle unchanged). Amounts standardized to Numeric(12,2); indexes on `fingerprint` and `(account_id, date)`. **No `snapshot_id`** — derived via `account_id → accounts.snapshot_id`. At personal-finance scale the join is free, and denormalizing would create a consistency invariant (import/txn snapshot must equal account snapshot) that buys nothing.

### merchant_cache, transaction_corrections, categories

Unchanged, deliberately **global** (not snapshot-scoped): classification knowledge and the category taxonomy are shared across households. Documented so cross-household classification behavior isn't a surprise.

### budget_entries / asset_entries

Finances' columns minus prefix; date columns become real `Date` types. `budget_entries` gains a nullable `category` column linking to ledger categories — projections use it to avoid double-counting scheduled expenses against estimated (trailing-average) category spend.

### balance_history (new)

```
id PK
account_id   FK accounts.id ON DELETE CASCADE, NOT NULL
as_of        Date NOT NULL
balance      Numeric(12,2) NOT NULL      -- canonical signed balance
available    Numeric(12,2) NULL
source       TEXT NOT NULL               -- 'statement' | 'manual' | 'migration'
import_id    FK imports.id NULL          -- set for source='statement'
note         TEXT NULL                   -- reconciliation notes
created_at   DateTime
UNIQUE (account_id, as_of, source)
```

Writes use SQLite upsert on the unique key: two statements covering the same as-of date collapse to the latest write; a manual edit on the same day as a statement coexists as a separate row. `accounts.balance`/`as_of_date` are a denormalized cache of the latest history row ordered by `(as_of DESC, created_at DESC)`, re-synced inside the same transaction on every write.

## Data migration: `fintrack migrate-legacy`

Config-driven (a mapping file is reviewable, repeatable, and testable; interactive prompts aren't):

```
fintrack migrate-legacy --spending-db spending.db --finances-db finances.db \
    --mapping mapping.yaml [--write-template mapping.yaml] [--dry-run]
```

1. `--write-template` scans both DBs (by reflection, not old models), auto-matches accounts (normalized name equality, then institution + partial-account-number/name-substring heuristics), and emits a YAML template: proposed pairs plus unmatched entries with `snapshot:` assignment slots (spending accounts are snapshot-less today; each gets assigned to a snapshot, defaulting to the primary one).
2. Apply order: snapshots ← `fin_snapshots`; accounts ← `fin_accounts` (`limit`→`credit_limit`, derive CC `balance = available − credit_limit`, parse date strings leniently — warn, don't crash); merge mapped spending accounts into those rows or create new rows for unmapped ones; remap `payment_account_ref`.
3. Copy categories (union with `configs/categories.yaml` seed), merchant_cache, imports/transactions/corrections (account/import FK remaps), budget/asset entries (`auto_account_ref`/`asset_ref` remaps).
4. Seed balance_history: every confirmed import with a `ledger_balance` → `source='statement'` row (as_of = `ledger_balance_date` or import date); each fin_account balance + as_of_date → one `source='migration'` row. `beginning_balance` is skipped (no reliable date). Re-sync `accounts.balance` from history ordering.
5. Verify: per-table row-count parity, `PRAGMA foreign_key_check`, and a liquid-total comparison old vs new.

## Web

Single Flask factory, port 5003 (`FINTRACK_PORT`), engine from `FINTRACK_DB`. Finances' URL-scoped pattern is adopted app-wide: `/s/<snapshot>/<section>`, root `/` = snapshot picker (redirecting when there's only one snapshot), `?edit=1` edit mode. Spending's routes move under the prefix and filter by the snapshot's account ids.

Navigation: `Status · Accounts · Transactions · Trends · Budget · Assets · Projections` plus a **Data** dropdown (Import, Merchants), snapshot picker, and theme toggle. Status remains the landing dashboard (six key numbers + funding), gaining a balance-freshness column.

Both HTMX idioms are kept per-page — they don't conflict: spending pages keep `HX-Request` partial swaps; finances pages keep spreadsheet cell editing. One `base.html` (Tailwind CDN + HTMX + Alpine.js + finances' theme handling); Alpine adopted globally. The accounts page becomes the merged editor: finances' grid over the unified columns plus per-account balance sparkline and as-of staleness.

## CLI

Click (spending's framework); finances' ~1,100-line argparse tree rewritten as Click groups. Global options: `--db` (default `FINTRACK_DB`), `--snapshot NAME` (default: sole snapshot, else required).

```
fintrack
  snapshots  list|add|rename|delete
  accounts   list|add|edit|delete
  balance    set <account> <amount> [--date] | history <account>
  import     run <files…> --account …
  staging    list|confirm|reject
  merchants … | categories … | report monthly|trends
  status | budget | income | expenses | assets | debts | funding
  project    [--months N]
  migrate-legacy …
```

## Balance history wiring

1. `confirm_import()` gains a post-confirm step: if the import carries `ledger_balance`, upsert a `statement` history row (+ available), re-sync the account. Lives in the repository layer so CLI and web share it via one `record_balance()` helper.
2. Manual balance edits (web edit-mode or `balance set`) write a `manual` history row; optional date allows backdating.
3. Accounts page: inline-SVG sparkline of recent history + as-of staleness coloring.
4. Reconciliation (informational, non-blocking): on statement confirm, compute `expected = previous balance + Σ(txn amounts between)`; if it differs from the statement balance beyond ε, store the delta in `note` and show an "unreconciled" badge.

## Projections

Monthly grid, default 12 months, starting from the latest balance per account in balance_history.

- **Recurrence engine**: month 0 uses finances' remainder-of-month proration; months 1..N use `_budget_entry_in_month` full-month semantics. Both move to `fintrack/budget/recurrence.py`; they already take `(year, month)`, so the only generalization is the multi-month loop with year rollover. The biweekly ≈ 2/month approximation is kept and documented.
- **Flows per month**: budget income/expenses land on `auto_account_ref` (entries without one hit a virtual "unassigned" bucket that still affects totals); CC autopay transfers the projected owed balance from `payment_account_ref` on `statement_due_day_of_month` (reusing funding-analysis logic).
- **Unscheduled-spend estimator (opt-in)**: trailing 3-month average ledger spend per category, minus categories claimed by budget entries via `budget_entries.category`, applied as an extra monthly expense.
- **Output**: accounts×months ending-balance table + inline-SVG line chart (liquid total, net worth), with warnings where an account dips below `minimum_balance`. Web: `/s/<snapshot>/projections` with horizon/estimator toggles as query params. CLI: `project --months N`.
- Reusable as-is from finances: `_budget_entry_in_month`, `_subtotal_remainder_of_month`, quarter/semiannual month logic, `account_funding_needed`, type→bucket map. Superseded: `projected_change_to_eom` (current-month only).

## Phasing (each phase = one PR, app working + CI green)

| Phase | Content | Verification |
|-------|---------|--------------|
| 0 | New repo, both histories merged; collision renames (`web_spending/`, `web_finances/`, `migrations_spending/`, `migrations_finances/`, `tests/spending/`, `tests/finances/`, per-app alembic inis); unified pyproject/mise/uv/CI; both apps run verbatim on their old ports/env vars | Both unit + e2e suites green; both servers boot; `git log --follow` shows both histories |
| 1 | Shared core: merged models (renames + balance_history), one db.py/config.py, domain code into subpackages, fresh Alembic baseline, legacy chains deleted | Ported unit tests green; `alembic upgrade` on empty DB ≡ `metadata.create_all` |
| 2 | `migrate-legacy` tooling | Fixture-DB unit tests; dry-run diff; real-DB run compared against legacy apps (counts, liquid total) |
| 3 | Unified web: single `web/`, `/s/<snapshot>/` everywhere, merged nav/base/theme, single Dockerfile CMD + CI publish | Merged e2e suite (keep `ANTHROPIC_BASE_URL`→closed-port trick); flow: pick snapshot → import OFX → confirm → txn appears → budget edit → status updates |
| 4 | Balance history: `record_balance()`, confirm-import hook, manual-edit hook, sparklines, reconciliation | Unit tests for upsert/latest-sync/delta; e2e: OFX with `LEDGERBAL` → balance + history point appear |
| 5 | CLI consolidation to Click; old entrypoints removed | CliRunner tests; smoke every group |
| 6 | Projections engine + estimators + page + CLI | Table-driven recurrence tests (year rollover; CC autopay conservation: Σ accounts constant across internal transfer); e2e renders |
| 7 | Docs rewritten from code (finances' stale docs not ported); classifier privacy constraint documented; old repos archived with pointer READMEs | — |

## Risks / gotchas

- Union `.gitignore` (`*.db`, `*.csv`, `*.ofx`, `*.qfx`, `data/` with `!data/example.yaml`) must land in the new repo's **first commit**, before history merges. (Verified: no real data is tracked in either repo today.)
- `limit`→`credit_limit` touches finances' repository/templates/CLI — done in Phase 1 while everything else moves.
- finances' string dates: parse leniently in migration (warn, don't crash).
- finances' FK-pragma listener attaches to the global `Engine` class — after Phase 1, spending code paths run with `foreign_keys=ON`; audit spending deletes (e.g. `delete_account`) in Phase 1 tests.
- Both test suites contain same-named modules (`test_navigation.py`, `test_accounts.py`) — test dirs become packages with `__init__.py` in Phase 0.
- Categories/normalization config paths are CWD-relative — centralized in `core/config.py` in Phase 1 so the CLI works from any cwd.
- Transactions use Numeric(10,2) vs (12,2) elsewhere — standardized to (12,2) in the baseline.
- Privacy constraint preserved verbatim: only normalized merchant names are ever sent to the Claude API.

## Out of scope

- Bank API sync (Plaid etc.) — imports remain file-based.
- Investment return modeling; debts decay by `interest_rate` is a possible later enhancement to projections.
- Multi-currency, multi-user auth.
