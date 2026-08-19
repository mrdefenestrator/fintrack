# fintrack — Design

fintrack is a single-user (multi-household) personal finance tracker built
from two merged predecessor apps: **spending** (transaction ledger) and
**finances** (net worth & cash flow). This document describes the system as
implemented; the original merge plan is preserved at
`docs/superpowers/specs/2026-07-15-fintrack-merge-design.md`.

## Architecture

fintrack is one Python application with two delivery surfaces over the same
domain and database:

```
Flask routes / Click commands
             │
             ├── workflow orchestration (routes, commands, importer)
             ├── pure domain logic (recurrence, calculations, projections)
             └── repositories ── SQLAlchemy Core ── SQLite
                       │
                       └── Alembic schema history
```

- **Delivery:** Flask routes translate HTTP/HTMX requests into repository and
  domain calls; Click commands do the same for the terminal. Jinja presenters
  and CLI table builders format results for their respective surfaces.
- **Workflow orchestration:** there is currently no separate application-service
  package. Multi-step use cases live in route/command functions or focused
  orchestrators such as `ledger/importer/run_import()`.
- **Domain logic:** recurrence, net-worth calculations, amortization,
  projections, normalization, and deduplication are kept independent of Flask
  and Click where possible.
- **Persistence:** repository modules own SQLAlchemy Core queries and mutations;
  routes and CLI commands do not construct SQL. Repositories expose legacy-shaped
  dictionaries (`type`, `asOfDate`, `assetRef`, etc.) while mapping to snake-case
  database columns at the repository boundary.
- **External adapters:** statement parsers read OFX/QFX/CSV files, and the
  classifier is the only integration with the Claude API.
- **Schema:** `fintrack/core/models.py` is the metadata definition used by the
  application and Alembic autogeneration. Alembic is the upgrade history used by
  deployments; `init_db()` also calls `MetaData.create_all()` at application/CLI
  startup so a new database can be bootstrapped. Legacy databases enter through
  `migrate-legacy`, never by upgrading a predecessor schema.

Two cross-cutting persistence patterns are central to the application:

- **Corrections overlay:** imported statement data is immutable; user fixes
  (category, merchant name, notes) live in `transaction_corrections` and are
  applied at read time.
- **Staging area:** imports are `staging` until reviewed, then `confirmed` or
  `rejected`; only confirmed transactions appear in reports.

### Transaction ownership

Write repositories and persistence helpers currently own their transaction
boundary: they issue their mutation(s) and call `Connection.commit()` before
returning. Callers normally open a connection with `engine.connect()` and rely
on the called writer to commit; read-only operations do not commit.

Consequently, one repository call is the practical unit of atomicity. A workflow
that invokes several committing writers—such as creating an import and then
inserting its transactions, or creating an account and then recording its
opening balance—crosses more than one transaction. Callers must not assume those
steps roll back as a unit. Preserve this convention when maintaining current
code; any move to workflow-level atomicity must be an intentional, coordinated
change that removes inner commits and makes a higher-level service own
`begin`/`commit`/`rollback`.

### Package layout

```
fintrack.py                # Click CLI entrypoint
fintrack/
  core/         engine/FK pragma, schema metadata, shared types, coercion,
                loading, ordering, filtering, formatting, CLI table builders
  ledger/       importer/ (OFX/QFX + CSV parsers, normalization, dedup),
                classifier.py (Claude API), repository/ (imports, transactions,
                merchants, corrections, categories, aggregations)
  accounts/     repository.py (unified accounts), balance_history.py
  budget/       repository.py, recurrence.py (occurrence + proration engine)
  networth/     repository.py (assets/debts), calculations.py (key numbers, funding)
  projections/  engine.py, estimators.py
  snapshots/    repository.py
  migrate/      legacy.py and yaml_import.py (one-time import adapters)
  cli/          Click command groups and shared CLI helpers
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
- `merchant_cache` and `categories` are deliberately **global**: classification
  knowledge and the category taxonomy are shared across snapshots. A merchant
  classified in one household is classified in all of them.
- `transaction_corrections` has no `snapshot_id`, but each correction belongs
  to exactly one transaction and therefore inherits that transaction's account
  and snapshot scope.
- `snapshot_id` foreign keys cascade on delete; ownership references between
  rows (`payment_account_ref`, `auto_account_ref`, `asset_ref`) use NO ACTION,
  so deleting a referenced row directly is blocked while a snapshot-level
  cascade still cleans up. SQLite runs with `foreign_keys=ON` (engine-level
  pragma listener).

### Tables

- **snapshots** — `name` (unique), `created_at`. A snapshot is an independent
  household; the whole app is scoped by it.
- **accounts** — merged from both predecessors: identity (`name` constrained
  unique per `(snapshot, institution)`, `institution`, `account_type`; partial
  account numbers live in the name itself, e.g. "Checking [1234]"), balance state
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
  below), `unit`, `value`/`quantity`/`balance`, valuation source, estimated
  return and monthly contribution, `asset_ref` (e.g. a loan against an asset,
  which surfaces as equity/LTV), `interest_rate`, loan origination fields, and
  `statement_due_day_of_month`.
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
(`fintrack/core/types.py`: `HOLDING_TYPE_TIER`; unknown/unset types default to
`illiquid`). A non-USD unit caps a nominally liquid type at `semi_liquid`:

- **liquid** — spendable now: checking, savings, wallets, gift cards, and
  credit cards (whose negative balance nets against cash).
- **semi-liquid** — brokerage and HSA holdings, plus symbol-denominated wallets
  such as cryptocurrency.
- **illiquid** — retirement, real estate, vehicles, loans, and unclassified
  holdings.

`fintrack/networth/calculations.py` reduces each holding to a **signed
contribution** (assets add, liabilities subtract; credit cards add rewards)
and sums them per tier. The tiers nest into cumulative totals
(`tiered_totals`): **liquid ⊂ investable ⊂ net worth**, where investable =
liquid + semi-liquid and net worth = every holding. A secured debt linked to
an asset via `asset_ref` also yields an **equity/LTV** pair (`equity_pairs`).

These power the **Holdings** page (`/s/<snapshot>/holdings`), an editable
unified sheet over accounts and asset/debt entries with tier totals, equity,
and filtering by type, balance side, and institution. The older per-domain key
numbers (`liquid_minus_cc`, `net_nonliquid_total`) remain available to CLI
reports and calculations alongside the tier totals; the standalone Accounts
and Assets web pages have been retired.

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
  cache of the latest history point ordered by `(as_of DESC, id DESC)`. The
  history upsert and cache update are committed together by `record_balance()`.
- **Reconciliation** (informational, non-blocking): on statement confirm, the
  expected balance (previous point + sum of transactions in between) is
  compared to the statement's; a mismatch beyond a half-cent tolerance is
  stored in `note` and surfaced as a badge in the UI.

Holdings surfaces balance dates and staleness; balance-history inspection is
also available from the CLI.

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
4. **Assets and debts** — non-debt assets apply their configured annual return
   and monthly contribution; debts with complete origination data amortize by
   their calculated payment on the configured due day.

Totals reuse the net-worth key-number calculations. Accounts dipping below
`minimum_balance` are flagged.
Surfaced at `/s/<snapshot>/projections` (horizon and estimator as query
params, inline-SVG chart) and `fintrack project --months N [--estimate]`.

Budget-vs-actual comparison (monthly budgeted amount, delta) is integrated
into the Trends page as Budget/mo and Delta columns alongside the existing
per-category spending averages; selected estimates can be included in the
projection engine as unscheduled spend.

## Web UI

Single Flask app (`web/app.py`), port 5003 (`FINTRACK_PORT`), database from
`FINTRACK_DB`. URL scheme: `/` is the snapshot picker; everything else is
`/s/<snapshot>/<section>`, with
`?edit=1` toggling spreadsheet-style edit mode on pages that support inline
editing.

Navigation is a **single-row top bar plus a collapsible side navigation**. The
top bar carries only a nav toggle (`☰`), the current page name, and the always-
visible utility controls — the lock/edit toggle, Import, the theme cycle, and
the snapshot picker. Primary navigation lives in a grouped outline
(`Finances`: Holdings · Budget · Projections; `Spending`: Trends ·
Transactions · Merchants · Categories — group headings are labels, only the
destinations are links). On wide screens (≥`lg`, 1024px) that outline is a
docked left sidebar;
the `☰` collapses it to reclaim horizontal width for the dense sheets. Below the
breakpoint it collapses to a hamburger that opens the same outline as an overlay
drawer, so a narrow header never clips. It is one responsive component (Alpine
state on `<body>`), not two nav systems — which is why it reads consistently
across desktop, mobile portrait, and mobile landscape (where the single top bar
is the big vertical reclaim). Holdings is the landing page. Holdings subsumes the
old standalone Accounts and Assets sheets, which were retired — it is one dense,
spreadsheet-style view over the same tables; information density is a deliberate
feature of these sheets, not a problem to design around. Net worth shows in the
Holdings sheet's footer (the header carries no figures). Holdings' structure and
the invariants that keep the sticky chrome correct are documented under
[Holdings sheet](#holdings-sheet) below. Import is a header icon; the edit-mode
lock is functional on Holdings, Budget, Transactions, Merchants, and Categories
and muted elsewhere. The old `/s/<snapshot>/status` dashboard was removed; its
URL redirects to Holdings.

Two route-scoping/HTMX idioms coexist. Transactions, trends, merchants,
categories, and import use a snapshot-scoped blueprint preprocessor
that places the validated filename and ID in `g`. Holdings, budget, and
projections validate an explicit filename argument. Ledger pages commonly swap
page-specific partials on `HX-Request`; editable sheets swap rows, cells, or
table bodies. One `base.html` provides Tailwind, HTMX, Alpine.js, navigation,
and theme behavior.

### Holdings sheet

Holdings is one table split into four **type-based** groups, rendered top to
bottom: **Cash · Credit Cards · Loans · Assets**. Cash and Credit Cards are type
slices of `accounts`; Loans combines account-type loans with `kind=debt`
`asset_entries`; Assets contains `kind=asset` entries. The split is a display
grouping only—there is no data migration—and debt↔asset equity/LTV pairing
(`calculations.equity_pairs`) is unchanged.

- **Ragged right edge with shared leading columns.** The first four columns —
  Institution·Type·Name·Amount — are shared across all groups and stay aligned
  via `<col>` widths. Each group appends its own trailing columns as real `<td>`
  cells; shorter groups pad with a filler `<td colspan>` to the widest group's
  column count so the HTML table grid stays consistent.
  Cash: Reserve·Funding·As Of. Credit Cards:
  Limit·Available·Rewards·Statement·Due·Linked·As Of. Loans:
  Interest·Equity·LTV·Original·Term·Originated·P&I·Paid·Due·Linked·As Of. Assets:
  Unit Price·Qty·Source·Est. Return·Mo. Contrib. Equity and LTV show on the
  **loan** row (the debt side of a secured pair). Original, Term, and Originated
  capture loan origination inputs; P&I and Paid are derived amortization values.
  Due is a recurring day of month, clamped to month-end when needed.
- **Row accent by group, not sign.** Cash, Credit Cards, Loans, and Assets have
  distinct green, rose, amber, and blue accents. A credit card therefore reads
  as a liability even at a zero balance rather than changing with the current
  amount's sign.
- **Totals.** Each group shows its own subtotal in its heading band; a master
  footer shows **Liquid** and **Net worth** (`calculations.tiered_totals`).
  Liquidity is a cross-cutting property, so it is reported independently of the
  row grouping rather than read off one group's subtotal.
- **Credit-card Available is computed and read-only** = `credit_limit + balance`.
  balance is the canonical input (kept current by statement imports); editing the
  limit or balance recomputes Available and preserves the balance
  (`accounts.repository._derive_cc_available`). Available ignores pending holds,
  so it reads slightly high — the deliberate tradeoff for never drifting from
  the imported balance.
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
group heading accents, and the row group accents are all
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
- Market-driven investment returns; variable-rate debt modeling.
- Multi-currency; multi-user auth.
