# fintrack — Design

fintrack is a single-user (multi-household) personal finance tracker built
from two merged predecessor apps: **spending** (transaction ledger) and
**finances** (net worth & cash flow). This document describes the system as
implemented; the original merge plan is preserved at
`docs/superpowers/specs/2026-07-15-fintrack-merge-design.md`.

## Architecture

- **SQLite** storage, **SQLAlchemy Core** (not ORM) for all queries,
  **Alembic** for migrations (single chain, fresh baseline — legacy data
  arrives via `migrate-legacy`, never via upgrade of an old DB).
- **Repository pattern**: all database access goes through repository modules;
  routes and CLI commands never build SQL.
- **Corrections overlay**: imported statement data is immutable; user fixes
  (category, merchant name, notes) live in `transaction_corrections` and are
  applied at read time.
- **Staging area**: imports are `staging` until reviewed, then `confirmed` or
  `rejected`; only confirmed transactions appear in reports.
- **Flask + HTMX** web UI, **Click** CLI, both thin layers over the same
  repositories.

### Package layout

```
fintrack.py                # Click CLI entrypoint
fintrack/
  core/         db.py (engine + FK pragma), models.py (single MetaData),
                config.py (config paths), types.py, formatting.py, filters.py
  ledger/       importer/ (OFX/QFX + CSV parsers, normalization, dedup),
                classifier.py (Claude API), repository/ (imports, transactions,
                merchants, corrections, categories, aggregations)
  accounts/     repository.py (unified accounts), balance_history.py, matching.py
  budget/       repository.py, recurrence.py (occurrence + proration engine)
  networth/     repository.py (assets/debts), calculations.py (key numbers, funding)
  projections/  engine.py, estimators.py
  snapshots/    repository.py
  migrate/      legacy.py (one-time legacy import), mapping.py
  cli/          one module per command group
web/            Flask app: app.py, routes/, templates/, static/
migrations/     Alembic (single chain)
configs/        categories.yaml, normalization.yaml, institutions/
tests/          unit tests by domain + tests/e2e/ (Playwright)
```

Config file paths are resolved relative to the package
(`fintrack/core/config.py`), so the CLI works from any working directory.

## Data model

Single `MetaData` in `fintrack/core/models.py`.

### Scoping conventions

- Every household-owned row belongs to a **snapshot**, either directly
  (`accounts`, `budget_entries`, `asset_entries`) or via its account
  (`imports`, `transactions`, `balance_history`). Imports and transactions
  deliberately carry **no** `snapshot_id` — it is derived through
  `account_id`, avoiding a denormalized consistency invariant.
- `merchant_cache`, `categories`, and `transaction_corrections` are
  deliberately **global**: classification knowledge and the category taxonomy
  are shared across snapshots. A merchant classified in one household is
  classified in all of them.
- `snapshot_id` foreign keys cascade on delete; ownership references between
  rows (`payment_account_ref`, `auto_account_ref`, `asset_ref`) use NO ACTION,
  so deleting a referenced row directly is blocked while a snapshot-level
  cascade still cleans up. SQLite runs with `foreign_keys=ON` (engine-level
  pragma listener).

### Tables

- **snapshots** — `name` (unique), `created_at`. A snapshot is an independent
  household; the whole app is scoped by it.
- **accounts** — merged from both predecessors: identity (`name` unique per
  `(snapshot, institution)`, `institution`, `account_type`; partial account
  numbers live in the name itself, e.g. "Checking [1234]"), balance state
  (`balance`, `available`,
  `credit_limit`, `rewards_balance`, `as_of_date`), autopay/funding config
  (`statement_balance`, `statement_due_day_of_month`, `payment_account_ref`
  self-FK, `minimum_balance`), and `sort_order`.
- **imports** — one row per statement file: `filename`, `file_hash`, `status`
  (`staging|confirmed|rejected`), plus captured statement balances
  (`ledger_balance(_date)`, `available_balance(_date)`, `beginning_balance`).
- **transactions** — immutable imported rows: `date`, `amount`,
  `raw_description`, `normalized_merchant`, `fingerprint` (indexed, also
  indexed on `(account_id, date)`).
- **merchant_cache** — normalized merchant name → `category`, with `source`
  (`api` or `manual`).
- **transaction_corrections** — at most one per transaction; overrides
  `category`, `merchant_name`, and/or `notes` at read time.
- **categories** — the shared taxonomy, seeded from `configs/categories.yaml`.
- **budget_entries** — scheduled income/expenses: `kind`, `amount`,
  `recurrence` plus its parameters (`date`, `day_of_month`, `month`,
  `day_of_year`, `continuous`), `auto_account_ref` (which account the money
  moves through), and a nullable `category` linking to the ledger taxonomy so
  projections don't double-count a budgeted expense against estimated
  category spend.
- **asset_entries** — assets and debts: `kind`, a liquidity-tier `type` (see
  below), `value`/`quantity`/`balance`, `asset_ref` (e.g. a loan against an
  asset, which surfaces as equity/LTV), `interest_rate`, `next_due_date`.
- **balance_history** — the time series behind `accounts.balance`; see below.

Money columns are `Numeric(12,2)` (asset values `14,2`); date columns are real
`Date` types.

### Canonical signed balance

`accounts.balance` is the canonical **signed** balance for every account type:
negative means owed (credit cards). This gives `balance_history` one semantics
across sources — OFX statements provide a signed ledger balance directly,
while credit-card editing in the UI works in available/limit terms and derives
`balance = available − credit_limit` on save. Calculations prefer `balance`
and fall back to available−limit.

### Liquidity tiers & holdings

Every holding — an `accounts` row or an `asset_entries` row — maps to one of
three **liquidity tiers**, fixed by its type with no per-holding override
(`fintrack/core/types.py`: `ACCOUNT_TYPE_TIER`, `ASSET_TYPE_TIER`; unknown
types default to `illiquid`):

- **liquid** — spendable now: checking, savings, wallets, gift cards, and
  credit cards (whose negative balance nets against cash).
- **semi-liquid** — brokerage, crypto, HSA, and `other` accounts.
- **illiquid** — retirement, real estate, vehicles, other assets, and loans.

`fintrack/networth/calculations.py` reduces each holding to a **signed
contribution** (assets add, liabilities subtract; credit cards add rewards)
and sums them per tier. The tiers nest into cumulative totals
(`tiered_totals`): **liquid ⊂ investable ⊂ net worth**, where investable =
liquid + semi-liquid and net worth = every holding. A secured debt linked to
an asset via `asset_ref` also yields an **equity/LTV** pair (`equity_pairs`).

These power the **Holdings** page (`/s/<snapshot>/holdings`), a read-only
unified view of accounts + assets with the tier totals, equity, and filtering
by tier / liabilities / institution. The older per-domain key numbers
(`liquid_minus_cc`, `net_nonliquid_total`, used by the Accounts/Assets pages
and projections) remain unchanged alongside the tier totals.

## Merchant classification & privacy

Merchant classification calls the Claude API (Haiku) with **only normalized
merchant names and the category list** — never amounts, dates, account
numbers, balances, or raw statement text. This is a hard constraint on
`fintrack/ledger/classifier.py`: the prompt is built exclusively from
`merchant_names` and `category_names`, and responses are constrained to the
category enum via a JSON-schema output format.

Classification is best-effort: results land in `merchant_cache`
(`source='api'`), already-cached merchants are never re-sent, and API failures
(missing `ANTHROPIC_API_KEY`, rate limits, connectivity) degrade to a warning
— imports never fail because classification is unavailable. Manual
assignments (`merchants set`, web edits) write `source='manual'` and take
precedence by simply overwriting the cache row.

## Import pipeline

1. **Parse** — OFX/QFX via `ofxparse`, CSV via per-institution configs
   (`configs/institutions/`). Statement balances (ledger/available/beginning)
   are captured onto the import row when the format provides them.
2. **Normalize** — merchant names cleaned via rules in
   `configs/normalization.yaml`.
3. **Dedup** — sequence-aware fingerprinting: a fingerprint of
   (account, date, amount, description) with an occurrence index, so two
   identical coffees on the same day survive but re-imported files don't
   duplicate. Duplicate files are also rejected outright by `file_hash`.
4. **Classify & stage** — uncached merchants from the batch are classified
   (best-effort) and the import lands in `staging` for review with categories
   already visible.
5. **Confirm/reject** — confirming records the statement balance into balance
   history; rejecting deletes the import's transactions.

## Balance history

Every balance write is a row in `balance_history`:
`source='statement'` (from a confirmed import carrying a ledger balance),
`'manual'` (web edit-mode or `balance set`, optionally backdated), or
`'migration'` (one-time legacy import). All writes go through one
`record_balance()` helper (`fintrack/accounts/balance_history.py`):

- **Upsert** on `UNIQUE (account_id, as_of, source)` — two statements covering
  the same as-of date collapse to the latest write; a manual edit and a
  statement on the same day coexist.
- **Re-sync**: `accounts.balance`/`available`/`as_of_date` are a denormalized
  cache of the latest history point ordered by `(as_of DESC, created_at
  DESC)`, updated in the same transaction as every write.
- **Reconciliation** (informational, non-blocking): on statement confirm, the
  expected balance (previous point + sum of transactions in between) is
  compared to the statement's; a mismatch beyond a half-cent tolerance is
  stored in `note` and surfaced as a badge in the UI.

The accounts page renders each account's recent history as an inline-SVG
sparkline with as-of staleness coloring (green ≤ 35 days, amber ≤ 95, red
beyond).

## Projections

`fintrack/projections/engine.py` produces a monthly ending-balance grid per
account (default 12 months, 1–60), starting from each account's current
balance. Per month, in order:

1. **CC autopay** — each credit card with a `payment_account_ref` pays its
   carried owed balance from that account: an internal transfer, so the sum
   across accounts is conserved. In the current month the transfer is skipped
   when `statement_due_day_of_month` has already passed.
2. **Budget flows** — each entry's occurrence amount for the month
   (remainder-of-month proration in month 0, full-month semantics afterwards —
   both in `fintrack/budget/recurrence.py`; biweekly is approximated as
   2/month) lands on its `auto_account_ref`; entries without one accumulate in
   a virtual "unassigned" bucket that still affects totals.
3. **Unscheduled spend** (opt-in `--estimate`) — trailing 3-month average
   ledger spend per category, minus categories claimed by budget entries and
   transfers, applied to the unassigned bucket (prorated in month 0).

Totals reuse the net-worth key-number calculations; assets are held constant
(no return modeling). Accounts dipping below `minimum_balance` are flagged.
Surfaced at `/s/<snapshot>/projections` (horizon and estimator as query
params, inline-SVG chart) and `fintrack project --months N [--estimate]`.

## Web UI

Single Flask app (`web/app.py`), port 5003 (`FINTRACK_PORT`), database from
`FINTRACK_DB`. URL scheme: `/` is the snapshot picker; everything else is
`/s/<snapshot>/<section>`, with
`?edit=1` toggling spreadsheet-style edit mode on the net-worth pages.

Navigation is two-tier and task-oriented: a primary row with two group tabs —
`Finances` (Holdings · Budget · Projections) and `Spending`
(Transactions · Trends · Merchants) — and a secondary row showing the active
group's sub-tabs. Holdings is the landing page; each group tab remembers its
last-visited sub-tab for the session (sessionStorage, `web/static/js/nav.js`).
Holdings subsumes the old standalone Accounts and Assets sheets, which were
retired — it is one dense, spreadsheet-style view over the same tables.
Information density is a deliberate feature of these sheets, not a problem to
design around. Net worth shows under the Holdings sub-tab (the "fintrack" title
carries no figure). Holdings' structure and the invariants that keep the sticky
chrome correct are documented under [Holdings sheet](#holdings-sheet) below.
Import is an icon button in the header rather than a tab, and the edit-mode
lock is functional only on the pages that honor it (Holdings, Budget — muted
elsewhere). The old `/s/<snapshot>/status` dashboard was removed; its URL
redirects to Holdings.

Two HTMX idioms coexist by design: ledger pages (transactions, trends,
merchants, import) use `HX-Request` partial swaps and are snapshot-scoped via
a `url_value_preprocessor` (`g.snapshot_id`); the net-worth pages (holdings,
budget) use finances-style spreadsheet cell editing with explicit template
names. One `base.html` carries Tailwind, HTMX, Alpine.js, and the light/dark
theme toggle.

### Holdings sheet

Holdings is one table split into four **type-based** groups, rendered top to
bottom: **Cash · Credit Cards · Loans · Assets**. Cash and Credit Cards are type
slices of the `accounts` table (credit cards split from spendable cash); Loans
and Assets are the `kind=debt` / `kind=asset` slices of `asset_entries`. The
split is a display grouping only — no data migration, and the debt↔asset
equity/LTV pairing (`calculations.equity_pairs`) is unchanged.

- **Columns are per-group and tight.** Each group carries its own header row and
  fills only its own columns; the leading Institution·Type·Name·Amount and the
  trailing Due·Linked·As Of slots sit in the same slot index in every group so
  they align down the table, and blank structural slots (rendered as empty cells
  with the normal gridline) pad the shorter groups — table width = the widest
  group, not the union. Cash: Reserve·Funding. Credit Cards:
  Limit·Available·Rewards·Statement·Due·Linked. Loans:
  Interest·Equity·LTV·Due·Linked. Assets: Unit Price·Qty·Source·Linked. Equity
  and LTV show on the **loan** row (the debt side of a secured pair). A column
  may carry a `span` so one cell covers several slots — the Assets "Source" cell
  spans three so its long valuation text borrows neighbouring slots instead of
  forcing the Rewards / Statement / LTV columns wide in the other groups.
- **Row accent by group, not sign.** The left asset/liability accent is green for
  Cash + Assets and red for Credit Cards + Loans — a credit card reads as a
  liability even at a zero balance — rather than keyed off the current amount's
  sign.
- **Totals.** Each group shows its own subtotal in its heading band; a master
  footer shows **Liquid** and **Net worth** (`calculations.tiered_totals`).
  Liquidity is a cross-cutting property, so it is reported independently of the
  row grouping rather than read off one group's subtotal.
- **Credit-card Available is computed and read-only** = `credit_limit + balance`.
  balance is the canonical input (kept current by statement imports); editing the
  limit or balance recomputes Available and preserves the balance
  (`accounts.repository._derive_cc_available`), while an `available` edit on the
  Accounts sheet still derives balance. Available ignores pending holds, so it
  reads slightly high — the deliberate tradeoff for never drifting from the
  imported balance.
- **Reorder is scoped per group.** Because two groups can share one table, a
  group's local drag permutation is mapped onto that group's *global* slots in
  the table, leaving the other group's rows fixed; a group whose rows span two
  tables is left non-reorderable rather than permuting both at once.

**Sticky spreadsheet chrome — the border invariant.** These sheets use
`border-collapse`, whose borders are painted in the *table's* own layer. When a
row is `position: sticky` — the group heading bands, the column-header rows, the
pinned total rows, and the left asset/liability accent — a collapsed border
scrolls out of view or paints over/beside the sticky cell (reproduced in both
WebKit and Chromium). **So no border on a sticky row may be a border-collapse
border**: draw it as a `box-shadow` on the cell instead (it paints with the
sticky element and stays put). Header underline + inter-column dividers, the
heading band's blue accent, and the row asset/liability accents are all
box-shadows (`web/templates/holdings.html`, `base.html`). Only the total row
stays pinned to the bottom; the add ("+ Add …") row flows inline as the last row
of its group. `web/static/js/sheet-scroll.js` pins the total(s) to the bottom
with a faint grid canvas and drives the four edge scroll-shadows (the side
shadows cover the column header but stop at the group title band).

## CLI

One Click tree (`fintrack.py`), with global `--db` (default `$FINTRACK_DB`)
and `--snapshot` (defaults to the sole snapshot; required when several exist):

```
snapshots  list|add|copy|rename|delete
accounts   list|add|edit|delete
balance    set <account> <amount> [--date] | history <account>
import     <files…> --account NAME
staging    list | confirm <id> | reject <id>
merchants  list | set
categories list|add|edit|delete
report     monthly | trends
status | budget | income | expenses | assets | debts | funding
project    [--months N] [--estimate]
migrate-legacy …
serve      [--port]
```

## Legacy migration

`fintrack migrate-legacy` merges the two predecessor SQLite databases into one
fintrack DB. It is config-driven (a mapping file is reviewable, repeatable,
and testable) and only ever reads the legacy databases:

1. `--write-template` reflects both DBs, auto-matches accounts (name equality,
   then institution/partial-account-number heuristics), and writes a YAML
   mapping: proposed pairs plus unmatched accounts with snapshot-assignment
   slots (spending's accounts were snapshot-less).
2. After review, the apply run copies snapshots, accounts (merging mapped
   pairs, `limit`→`credit_limit`, deriving signed CC balances, parsing legacy
   string dates leniently), categories, merchant cache, imports, transactions,
   corrections, and budget/asset entries, remapping all cross-references.
3. Balance history is seeded from confirmed imports that captured a ledger
   balance (`source='statement'`) and from each legacy account's balance +
   as-of date (`source='migration'`).
4. Verification: row-count parity per table, `PRAGMA foreign_key_check`, and a
   liquid-total comparison against the legacy apps. `--dry-run` runs the whole
   migration in a transaction and rolls back.

## Testing & CI

- Unit tests by domain under `tests/`; e2e Playwright tests under `tests/e2e/`
  (marked `e2e`). E2e classifier tests point `ANTHROPIC_BASE_URL` at a closed
  port so no real API calls can escape.
- `mise run test` = format check + lint (ruff, 88 cols, E501 ignored) + unit
  tests with coverage + e2e.
- GitHub Actions runs the same tasks and publishes the Docker image
  (`fintrack`) on pushes to `main`. The container applies Alembic migrations
  on startup.

## Out of scope

- Bank API sync (Plaid etc.) — imports remain file-based.
- Investment return / debt interest modeling in projections.
- Multi-currency; multi-user auth.
