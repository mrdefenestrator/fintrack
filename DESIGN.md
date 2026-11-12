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
  ledger/       importer/ (OFX/QFX + CSV parsers, normalization, dedup,
                near-duplicate matching), duplicate_scan.py, classifier.py
                (Claude API), repository/ (imports, transactions, merchants,
                corrections, duplicates, categories, aggregations)
  accounts/     repository.py (unified accounts), balance_history.py
  budget/       repository.py, recurrence.py (occurrence + proration engine)
  networth/     repository.py (assets/debts), calculations.py (key numbers, funding)
  projections/  engine.py, estimators.py
  insights/     overview.py (Overview page + `status` numbers), cash_flow.py
                (monthly income/spending/savings rate: Trends, Overview, CLI)
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
  (`holdings`, `budget_entries`) or via its holding (`imports`,
  `transactions`, `balance_history`). Imports and transactions deliberately
  carry **no** `snapshot_id` — it is derived through `account_id` (a holding
  id), avoiding a denormalized consistency invariant.
- `merchant_cache` and `categories` are deliberately **global**: classification
  knowledge and the category taxonomy are shared across snapshots. A merchant
  classified in one household is classified in all of them.
- `transaction_corrections` and `transaction_duplicates` have no
  `snapshot_id`, but each row belongs to exactly one transaction and
  therefore inherits that transaction's account and snapshot scope.
- `snapshot_id` foreign keys cascade on delete; ownership references between
  rows (`payment_account_ref`, `auto_account_ref`, `secured_asset_ref`) use
  NO ACTION, so deleting a referenced row directly is blocked while a
  snapshot-level cascade still cleans up. These refs are **composite**
  (`(ref, snapshot_id)` → the target's `(id, snapshot_id)`), so a reference
  can never cross snapshots, and the typed ones point at the right detail
  table (a payment account must be a cash holding, a secured asset an asset
  holding). SQLite runs with `foreign_keys=ON` (engine-level pragma listener).

### Tables

- **snapshots** — `name` (unique), `created_at`. A snapshot is an independent
  household; the whole app is scoped by it.
- **holdings** (supertype) — every account and asset/debt is one row here,
  carrying only the shared spine: `snapshot_id`, `group_key`
  (`cash|credit_card|loan|asset`), `type` (the liquidity-tier vocabulary),
  `name`, `institution`, `as_of_date`, and a per-`(snapshot, group_key)`
  `sort_order`. CHECK constraints keep `type` consistent with `group_key`; a
  partial unique index makes importable holdings (`group_key != 'asset'`)
  unique per `(snapshot, institution, name)`, the rule the OFX
  create-account flow relies on. Two extra unique keys (`(id, group_key)`,
  `(id, snapshot_id)`) exist purely as composite-FK targets.
- **cash_details / credit_card_details / loan_details / asset_details**
  (subtypes) — one row per holding, keyed by `holding_id` (PK = FK to
  `holdings`), holding only that group's columns:
  - *cash* — `balance`, `minimum_balance` (reserve target).
  - *credit_card* — `balance`, `credit_limit`, `rewards_balance`,
    `statement_balance`, `statement_due_day_of_month`, `payment_account_ref`
    (→ a cash holding). Available is **not stored** — it is computed as
    `credit_limit + balance`.
  - *loan* — `balance`, `interest_rate`, loan origination fields
    (`original_principal`, `term_months`, `origination_date`),
    `statement_due_day_of_month`, `payment_account_ref` (→ cash) and
    `secured_asset_ref` (→ an asset). Loans that used to be account rows and
    loans that used to be debt entries are the same shape here.
  - *asset* — `unit`, `quantity`, `value`, valuation `source`,
    `annual_return_rate`, `monthly_contribution`.
  Each detail row denormalizes `group_key` and `snapshot_id` from its spine
  as composite-FK material: the DB rejects a wrong-group detail row, and a
  group change on the spine is rejected while a detail row exists (so the
  repositories do delete-detail → move-spine → insert-detail).
- **imports** — one row per statement file: `account_id` (a holding id) plus
  a denormalized `holding_group` (constrained to the importable groups, kept
  in sync by ON UPDATE CASCADE, so a holding with import history can't be
  retyped into the asset group), `filename`, `file_hash`, `status`
  (`staging|confirmed|rejected`), plus captured statement balances
  (`ledger_balance(_date)`, `available_balance(_date)`, `beginning_balance`).
- **transactions** — immutable imported rows: `date`, `amount`,
  `raw_description`, `normalized_merchant`, `fingerprint` (indexed, also
  indexed on `(account_id, date)`), and `external_id` — the institution's own
  transaction id (OFX `FITID`; NULL for CSV and for rows imported before ids
  were kept), indexed on `(account_id, external_id)`.
- **merchant_cache** — normalized merchant name → `category`, with `source`
  (`api` or `manual`).
- **transaction_corrections** — at most one per transaction; overrides
  `category`, `merchant_name`, and/or `notes` at read time, and carries the
  optional `budget_entry_ref` linking the transaction to the budget entry it
  realizes (see "Transaction–budget association"). The FK is `ON DELETE SET
  NULL`, so deleting a budget entry unlinks its transactions rather than
  deleting them; correction rows that held only that link are purged first, and
  unlinking prunes a row left with every overlay column NULL. A row that exists
  only for a link is not a "correction": the Transactions Status filter keys on
  category/merchant/notes being set, not on the row existing.
- **transaction_duplicates** — possible-duplicate flags (see "Duplicate
  detection"): at most one per transaction (`transaction_id` unique), naming
  the earlier row it resembles (`duplicate_of_id`), a human-readable
  `reason`, `source` (`import|scan`), `status`
  (`flagged|duplicate|not_duplicate`) and `resolved_at`. An overlay like
  corrections — raw rows never change. Both FKs cascade, so rejecting either
  side's import clears the flag. Both rows always share an account, which
  keeps them in one snapshot.
- **alert_dismissals** — dismissed spending alerts (see "Spending alerts"):
  `snapshot_id` plus either a `transaction_id` (every alert on that row) or a
  `category` + `month` (that category's running-hot alert for one month, the
  month's 1st); a CHECK allows exactly one of the two, and each is unique per
  snapshot. The alerts themselves are computed, never stored. Both FKs
  cascade.
- **link_suggestion_dismissals** — `(transaction_id, budget_entry_ref)` pairs
  the user rejected as link suggestions, unique per pair; both FKs cascade.
  Snapshot consistency is checked in `reconcile`, as for the link itself.
- **recurring_dismissals** — merchants the user marked "not recurring" (see
  "Recurring charges"), unique per `(snapshot_id, merchant)`; keyed by the
  resolved merchant name, since merchants are global. Cascades with the
  snapshot.
- **categories** — the shared taxonomy, seeded from `configs/categories.yaml`.
- **budget_entries** — scheduled income/expenses: `kind`, `amount`,
  `recurrence` (`one_time|monthly|weekly|biweekly|quarterly|semiannual|annual`;
  weekly counts 52/12 times a month, biweekly keeps the legacy 2) plus its
  parameters (`date`, `day_of_month`, `month`,
  `day_of_year`, `continuous`), `auto_account_ref` (which account the money
  moves through), and a nullable `category` linking to the ledger taxonomy so
  projections don't double-count a budgeted expense against estimated
  category spend.
- **balance_history** — the dated value of every holding (cash and card
  balances, signed loan balances, asset values) and the time series behind a
  holding's cached balance; see below. Its `account_id` is a holding id in
  any group.

Money columns are `Numeric(14,2)`; date columns are real `Date` types.

The dict API the repositories expose (the `Account` / `AssetEntry`
TypedDicts, field names like `limit`, `asOfDate`, `assetRef`) is unchanged by
the supertype split: routes, templates, calculations, and the CLI still work
off flat dicts. The two-level table mechanics live behind
`fintrack/core/holdings.py` and the domain repositories. The legacy migration
predates the split; assets/debts still enter only via `fintrack
migrate-legacy` or `yaml_import`, both of which now write the split schema.

### Canonical signed balance

A holding's detail `balance` is the canonical **signed** balance: negative
means owed (credit cards and loans). This gives `balance_history` one
semantics across sources — OFX statements provide a signed ledger balance
directly (except loan servicers, which report principal owed as positive;
`create_import` stores a loan's statement balance as `-abs()`), while
credit-card editing in the UI works in limit/balance terms and available is computed (`credit_limit + balance`) rather than stored, so it
never drifts from the imported balance. The debt-side dict API still presents
a loan's amount owed as positive; the repository negates at the boundary.

### Liquidity tiers & holdings

Every holding — one `holdings` row — maps to one of three **liquidity
tiers**, fixed by its type with no per-holding override
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
3. **Dedup** — exact matches are skipped; near-matches are flagged for
   review (see "Duplicate detection" below). Duplicate files are rejected
   outright by `file_hash`.
4. **Classify & stage** — uncached merchants from the batch are classified
   (best-effort) and the import lands in `staging` for review with categories
   already visible.
5. **Confirm/reject** — confirming records the statement balance into balance
   history; rejecting deletes the import's transactions.

## Duplicate detection

Issue #82: institutions re-export the same transactions with reworded
descriptions (Apple Card did), so exact text matching alone lets copies in.
Detection is layered, strictest first, and runs entirely locally — no
transaction data is sent to the Claude API.

1. **Bank transaction id.** An incoming row whose `external_id` (OFX `FITID`)
   already exists in the account is skipped, whatever its description says.
2. **Fingerprint.** Sequence-aware: a hash of (account, date, amount, raw
   description) with an occurrence index over the whole file, so two
   identical coffees on the same day survive but a re-imported file doesn't
   duplicate. Exact matches are skipped. The bank-id pass runs after it, so
   whole-file sequence numbers stay intact.
3. **Near-duplicate flag.** Everything still incoming is paired against
   existing rows in the account (`fintrack/ledger/importer/near_dup.py`):
   same amount to the cent, dates within 3 days, and a similar merchant —
   identical normalized names, or at least 60% `difflib` similarity on the
   raw or the normalized text (normalization strips store numbers and
   processor prefixes, which raw text scores poorly against). Pairing is
   one-to-one per import — closest date, then highest similarity — so two
   real identical charges in both files don't collapse onto one row. Rows an
   exact match already accounted for, and rows that are themselves flagged
   or marked duplicate, are never offered as the original. Differing bank
   ids don't rule a match out: some institutions regenerate ids on re-export,
   and a false flag costs one click while a missed copy is silent. A
   same-day repeat (a second identical row where the first already exists)
   is flagged against its first-occurrence sibling. A match is **only ever a
   flag** — the row is inserted and shown for review.

Flags live in `transaction_duplicates`. The user resolves each one in the
staging review, on the Transactions sheet ("Possible duplicate" status
filter), or with `fintrack duplicates mark|dismiss|undo`. **`duplicate`
hides the row from every confirmed-transaction read** — transaction lists,
spending totals, budget actuals and link dates, merchant stats, latest
transaction dates, and the statement reconciliation sum — through one
clause, `ledger.repository.duplicates.not_hidden_duplicate()`; any new
reader of confirmed transactions must apply it (or go through
`base_transaction_query`). The "Marked duplicate" filter is the one view
that shows hidden rows, with Undo. `not_duplicate` keeps the row and stops it
being re-flagged.

Copies that got in before flagging existed are found by a **scan**
(`fintrack duplicates scan [--apply]`, or "Scan for duplicates" on the Import
page), which replays import-time matching over confirmed data: per account,
imports in arrival order, each paired against earlier imports. Rows that
already carry any flag are left alone, so re-running is idempotent and
dismissals stick. The older `fintrack dedup` still collapses exact
fingerprint copies left by a past bug.

## Spending alerts

Issue #101: point out spending that stands out while it still matters. Three
local checks, pure functions in `fintrack/ledger/alerts.py` over the
snapshot's confirmed history (`get_alert_history`, built on
`base_transaction_query`, so hidden duplicates are skipped). **No transaction
data goes to the Claude API.** Spending categories only: Income and Transfer
never alert and never count as history.

1. **Large charge** — a charge over 2× the merchant's median earlier charge and
   at least $50 above it, given 3+ earlier charges; with less merchant history,
   over 3× the category's median and $100 above it.
2. **New merchant** — the first-ever charge from a merchant (no earlier row of
   any sign), $50 or more, once the snapshot has 60+ days of history (so a
   first import doesn't flag every merchant).
3. **Running hot** — from the 7th, a category's month so far projects to 1.3×
   its plan and $100 over. The projection follows the month's usual shape, so
   lump sums aren't mistaken for a pace: a budgeted category expects its
   fixed lines in full and its continuous (variable) lines pro rata, as Budget
   vs Actual does; an unbudgeted one expects what it had spent by the same
   day, on average, over the last 6 completed months with data (3 needed).
   Spending so far is scaled by plan ÷ expected-by-now (or, when nothing is
   usually in by now, the usual month is added on top). Charges that carry
   their own alert are left out, so one big charge isn't reported twice.

Charge checks look at the last 30 days, so alerts age out on their own; a row
carries at most one (large charge wins). Every threshold lives in
`configs/alerts.yaml` (`AlertConfig`; unknown keys are an error).

`fintrack/insights/alerts.py` `get_alerts()` gathers the data, runs the
checks, and drops dismissed alerts (`alert_dismissals`). One list feeds every
surface:

- **Overview "Spending alerts" card** — one line per alert (five, then "+N
  more"); the whole card opens Transactions on Status = **Flagged**. It is its
  own card rather than part of Needs attention (#100, #102), because a card
  is one link to one view and these alerts live on a different view.
- **Transactions** — the **Flagged** status filter shows the rows behind
  every open alert (`alerts_by_transaction()`): a large or new-merchant
  charge's own row, and this month's rows in a category running hot, so the
  card's one link never opens an empty view. It is computed, so the route
  passes the ids to `get_transactions(transaction_ids=…)`. A flagged charge
  takes the orange tone and a "⚑ Large charge · usually $50" / "⚑ New
  merchant" line under its description with **Dismiss** (then Undo until
  reload), like the duplicate badge. A row flagged by its category gets a "⚑
  Dining running hot this month · $420 of usually $600" line but no tone, so
  a hot category doesn't light up the sheet; its button reads "Dismiss for
  Dining" and hides the category for the month, reloading the view, clearing all its rows at once (undo
  with `fintrack alerts undo --category`).
- **Import confirmation** — "N things to look at in this import" when the
  just-confirmed rows carry open alerts, linking to Flagged.
- **CLI** — `fintrack alerts [list] [--all]`, `alerts dismiss <txn> |
  --category NAME`, `alerts undo …`; `status` prints the card.

Dismissing hides an alert everywhere. A running-hot alert is otherwise
cleared by the pace dropping or the month ending. A count next to Overview in the nav (ticket part D)
waits until the alerts prove few and useful.

## Balance history

Every value change of every holding is a row in `balance_history`, so any
holding's value on a past date can be read back (#107):
`source='statement'` (from a confirmed import carrying a ledger balance),
`'manual'` (web edit-mode, `balance set`, or a loan/asset add or edit,
optionally backdated by its As Of), `'price'` (a price refresh re-valued a
priced asset), `'baseline'` (the one-time starting point every loan and asset
got when this shipped, migration `e7f8a9b0c1d2`, and that `migrate-legacy`
gives the loans and assets it creates), or `'migration'` (one-time legacy
import of account balances). All writes go through one `record_balance()`
helper (`fintrack/accounts/balance_history.py`):

- **What `balance` holds**: the signed balance for cash, cards and loans
  (negative = owed); for an asset, its value — quantity × the cached price
  for a non-USD unit, else quantity × the manual per-unit value — the same
  amount the Holdings sheet shows. `fintrack/networth/repository.py` records a
  point when a loan's balance or an asset's value, quantity or unit changes
  (nothing when the value is unchanged). Writing a price into the price cache
  (`prices._write_cache`, behind both the stale-price refresh on load and the
  on-demand refresh control) calls `record_price_points()`, which records a
  `'price'` point for every asset held in that symbol, in every snapshot (the
  cache is global), skipping unchanged values.
- **Upsert** on `UNIQUE (account_id, as_of, source)` — two statements covering
  the same as-of date collapse to the latest write; a manual edit and a
  statement on the same day coexist. **Assets keep one point per day**
  instead: their value has one source of truth, so a second write that day
  updates the first. A price refresh keeps the day's existing source; a
  manual edit marks it `'manual'`.
- **Re-sync**: the holding's detail `balance` (assets have none) and its
  spine `as_of_date` are a denormalized cache of the latest history point
  ordered by `(as_of DESC, id DESC)`, ignoring `'price'` points — a price
  refresh re-values an asset but says nothing about when the user last
  checked it, so it doesn't move As Of. The history upsert and cache update
  are committed together by `record_balance()`.
- **Reading a past value**: `balances_on(conn, holding_ids, on)` returns each
  holding's latest point dated on or before `on` (holdings with none are left
  out). Values carry forward between points, so a month-end read is just
  `balances_on(..., last_day_of_month)`. Loans are flat between edits — their
  history is what the user recorded, not an amortization estimate.
- **Reconciliation** (informational, non-blocking): on statement confirm, the
  expected balance (previous point + sum of transactions in between) is
  compared to the statement's; a mismatch beyond a half-cent tolerance is
  stored in `note` and surfaced as a badge in the UI.

Holdings surfaces balance dates and staleness; balance-history inspection is
also available from the CLI (`balance history` for accounts, `assets history`
and `debts history` for assets and loans — debts shown as the positive amount
owed, like the rest of the debts commands).

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

The result also carries the starting point ("now": each row's current
balance, `start_liquid`, `start_net_worth`) and a flow list per month — every
balance movement the engine applies (autopay, budget income/expense, the
estimate, asset growth/contribution, loan interest/payment), tagged with the
holding it lands on (none = unassigned). A row's change in a month is exactly
the sum of its flows. `summarize()` turns a result into the headline numbers:
liquid and net worth now → end of horizon (change and per-month average), the
lowest liquid month, loans paid off within the horizon, and minimum-balance
warnings collapsed to one line per account.

Surfaced at `/s/<snapshot>/projections` and `fintrack project`, as one
**timeline** of actual past and projected future months (#98) — see
[Timeline](#timeline) below. The web page leads with a one-line summary
(liquid and net worth at the end of the window) — the full summary and the
warnings live on [Overview](#overview) — then the grid and, with the estimate
on, its per-category breakdown. The CLI leads with the full summary and prints
the warnings to stderr.
A Balances / Changes toggle (`?view=change`, CLI `--changes`) swaps the grid's
month-end balances for each month's change, so lumpy months stand out;
below-minimum cells stay marked by balance in either view. The web grid is a
grouped sheet like Holdings (see [Projections sheet](#projections-sheet)). An
icon in each projected month's header (`?month=YYYY-MM`, CLI `--month
YYYY-MM`) opens that month's breakdown above the grid: its flows by account
(`month_breakdown()`), with money in, money out, card autopay, and asset/loan
totals. Past months have no breakdown yet (listing their actual transactions
per account is a possible follow-up).

### Timeline

`fintrack/projections/timeline.py` `build_timeline()` fills a window of 6 / 12
/ 24 months (`?months=`, CLI `--months`; default 12) starting at `?from=YYYY-MM`
(CLI `--from`; default the current month):

- **Past months** (before the current one) show actual month-end values: each
  holding's latest `balance_history` point on or before the month's last day
  (`month_end_points()`, the batched form of `balances_on()`). A holding with
  no point yet is **blank**, not zero, and that month's band subtotal and
  totals carry a `*` (CLI: `*`) saying how many holdings are missing. A value
  whose point is from an earlier month is **carried forward** and shows dimmed
  with "last recorded <date>" (CLI: `~`). Past totals use the engine's
  formulas: liquid = `liquid_total`, net worth = `liquid_minus_cc` + asset
  values + (negative) loan balances. Unassigned has no history, so it's blank
  in the past.
- **The current month on** is the projection, run from now through the
  window's end (the window is pulled back so it ends within the 60-month
  horizon). A **Now** column (current balances) sits just before the current
  month when the window spans today.
- **Changes**: a month's change is its value minus the previous month's; the
  current month is measured from Now (what's still to come, as before), and a
  missing value or partial total on either side shows no change.
- **Summary line**: a window ending in the future shows the projection's end
  values and change from now ("By May 2027"); one ending in the past shows the
  actual values then and the change since ("End of Jun 2026 … (+$X since)").
- **Paging**: ◀ / ▶ move by one window (`nav.pager`, shared with Trends); ◀
  stops once the window reaches the earliest recorded point, ▶ at the horizon;
  **Now** returns to the window starting this month.

There is deliberately no chart: one shared axis made the small, meaningful
movements (cash, cards) unreadable next to houses and mortgages (#54).

Budget-vs-actual comparison (monthly budgeted amount, delta) is integrated
into the Trends page as Budget/mo and Delta columns alongside the existing
per-category spending averages; selected estimates can be included in the
projection engine as unscheduled spend.

## Transaction–budget association

Beyond the category-level rollup, an individual transaction can be linked to
the specific budget entry it realizes (`fintrack/budget/reconcile.py`). The
link is a nullable `budget_entry_ref` on the corrections overlay, so raw
transactions stay immutable and the one-row-per-transaction shape enforces the
cardinality: a transaction realizes **at most one** budget entry; an entry is
realized by **many** transactions over time. The occurrence a transaction
belongs to is derived from its date, not stored. Snapshot consistency (the
transaction and entry share a snapshot) is validated in the repository, since
corrections carry no `snapshot_id` and the composite-FK guard used elsewhere is
unavailable here.

**Reversals.** Normally a charge realizes an expense entry and a deposit an
income entry. A transaction of the opposite sign may also link — a refund on an
expense line, a clawback on an income line (`reconcile.is_reversal`). A
reversal nets against the entry's actual (charges − refunds, so a month can go
negative) but is never treated as the money having moved: it doesn't count
toward N, can't satisfy the missed-charge check (a refund alone leaves a
tracked bill **missing**, and can't make an entry tracked or set its Last
seen date), is excluded from price drift, doesn't realize an
occurrence for projection true-up, and is never suggested. Like any link it
pins the entry's category, so a refund also nets that category's totals.

Three things build on the link:

- **Suggestions** (`suggest_links`) — a purely local heuristic (no Claude API,
  so the classifier privacy constraint is untouched) scores each unlinked
  transaction against candidate entries on amount closeness (gated to ±25%),
  category match, flow-account match, and date proximity to the expected
  occurrence, sharpened by detected recurring series (see "Recurring
  charges"). Entries measured by category total (below) are never proposed:
  their category already counts the transaction, and a link would only re-pin
  its category. Suggestions are always presented for confirmation, never applied
  silently. A rejected suggestion can be **dismissed**: the pair is stored in
  `link_suggestion_dismissals` and never proposed again (the transaction may
  still be offered its next-best entry, and manual linking is unaffected). On
  the Budget vs Actual page each entry shows how many of the month's unlinked
  transactions look like it; in edit mode a row under the entry lists them with
  Apply / Dismiss. The CLI has `transactions suggest [--apply]` (y / n / d per
  suggestion) and `transactions dismiss` / `undismiss`.
- **Budget-vs-actual per entry** (`budget_actuals`) — for a month, each entry's
  scheduled amount is compared to its actual and classified matched / over /
  under / on pace / missing / upcoming / unlinked / inactive. Discrete entries (bills,
  paychecks) are measured by their linked transactions, exposing price drift (a
  linked charge that differs from the budgeted amount). A variable, continuous
  entry that alone claims its category is measured by that category's month
  total instead (nobody links every coffee to "Dining Out"). **Missing** means a
  *tracked* entry — one linked in an earlier month — whose charge didn't show
  up; an entry that has never been linked is **unlinked**, because absent links
  say nothing about whether the money moved. Each result carries a tone
  (good / bad / neutral) so over/under read correctly for both kinds: earning
  over budget is good, spending over it is bad. In the month in progress a
  category-measured variable line is judged against its **pace** — expected ×
  the share of the month gone (days elapsed, today included) — rather than the
  full month: within 10% of pace it is **on pace**, further ahead **over**
  (also once past the month's full amount), further behind **under**; the
  page shows the pace next to the status. Past months and linked bills use the
  full expected amount. Figures are **signed** like the Budget sheet (income
  +, expenses −), and so is Delta (actual − expected, signed), so a + Delta
  beats the budget and the Net footer is the sum of the rows (#103). Surfaced
  on its own
  Budget vs Actual page (`/s/<snapshot>/budget/actual`, read-only apart from
  the edit-mode link suggestions, in the
  Spending nav group; month pager, Kind/Category/Status filters, and a net
  income − expenses total row, laid out like the Transactions sheet) and `fintrack transactions budget-actual`.
- **Projection true-up** — in the current (partial) month, a budget flow already
  realized as a linked transaction is reflected in the starting balance, so the
  projection engine caps that month's remaining scheduled amount by what is
  still unrealized instead of double-counting it (a more precise complement to
  the day-based remainder and the category-exclusion estimator).

Manual linking is available on the Transactions sheet (a Budget-entry picker per
row) and via `fintrack transactions link/unlink`. The picker leads with the
row's own kind (an expense → expense entries), the row's category first, with
other same-kind entries below as an escape hatch (linking to one re-pins the
category). Opposite-kind entries follow in separately labeled groups
("Refund · …" / "Clawback · …"), and a linked reversal reads e.g.
"Rent (refund)" in the Budget cell. Budget vs Actual notes each line's netted
reversals beside its actual; the CLI's `budget-actual` has a Reversed column.

**A linked entry pins the transaction's category.** Category and the budget link
are two axes — classification vs. which planned line a transaction realizes —
but they nest, because every budget entry carries a category. So when a
transaction is linked to an entry that has a category, that category becomes the
transaction's resolved category, ahead of the per-transaction correction and the
merchant cache: `coalesce(budget_entry.category, correction.category,
merchant_cache.category, 'Uncategorized')` (`aggregations._resolved_category`).
This makes linking the more specific statement of intent — it can't contradict
the category, and Trends/estimator counts a linked transaction under the line it
funds. The Transactions sheet renders the Category cell read-only (inherited)
while a categorized link is in place; unlink to edit it directly. An entry with
no category doesn't clobber the merchant classification.

### Recurring charges

`fintrack/budget/recurring.py` (issue #100) finds subscriptions and bills the
budget doesn't cover, and recurring prices that changed. Like the suggester it
is purely local — no transaction data goes to the Claude API.

- **Detection.** Over the last 400 days (so a yearly charge shows twice), a
  merchant's charges — and separately its deposits, reported as income — are
  *recurring* when the gaps between them fit one cadence (weekly 5–9 days,
  biweekly 11–17, monthly 25–36, yearly 340–390; at most one gap in five off,
  none shorter), at least 3 times (2 for yearly), for a steady price (within
  10%), and the last charge is recent (within ~1.5 intervals; older series
  look cancelled). Amounts may step to a new price: the series splits into
  runs of same-price charges, and up to three runs are allowed when the middle
  ones have at least two charges each. Transfers are excluded. Reads go through
  `get_transactions`, so hidden duplicates and unconfirmed imports never count.
- **Against the budget.** Each series is `linked` (its transactions link to an
  entry), `unlinked` (an entry names the merchant, or shares its category,
  recurrence and price, but nothing is linked), `covered` (a variable line
  measured by category total claims its category), or `not_budgeted`.
- **Price change.** When the latest run is a new price that started within
  ~3 months (two intervals for longer cadences), the series reports
  previous → current "since" that date, budgeted or not — the unlinked
  complement to Budget vs Actual's per-entry drift.
- **Actions.** *Add to budget* creates an entry from the series (current price,
  cadence → recurrence, day of month / month from the last charge, category,
  usual account) and links every charge in it; *Link* links them to the
  `unlinked` match; *Not recurring* stores the merchant in
  `recurring_dismissals` and hides it.

Surfaced as Budget vs Actual's **Recurring** view (`?view=recurring`, a
By month / Recurring toggle above the sheet): a flat grouped sheet of every
series — Merchant · Category · Typical · Every · N · Last · Next · Budget ·
Price change — with Kind and Status filters (Status includes a "Price change"
flag) and a rough per-month footer; in edit mode the actions cell offers
+ (add) or a chain (link), and × (not recurring). The Overview's Needs
attention card and `fintrack recurring` show the same findings.

**Feeding link suggestions** (#119). `suggest_links` also reads the detected
series (`recurring.series_index`, as of the end of the month in scope, so a
past month is judged by the history it had), on top of the base weights and
capped at 1.0:

- a charge whose series is already **linked** to an entry gets +0.30 toward
  that entry, and its amount is also judged against the series' current price
  ("amount fits series"), so a price step past the ±25% gate is still proposed
  (flagged as drift);
- the **newest** charge of an `unlinked` series gets +0.20 toward the entry it
  matches — linking it makes the series linked, which then lifts the rest;
- a merchant known **not to recur** — at least 3 charges in the window and no
  cadence, or marked "Not recurring" — gets −0.20 against discrete recurring
  entries (weekly/biweekly/monthly/annual, not continuous). A merchant seen
  only once or twice is unknown, not irregular, and isn't penalized.

The reasons column names each signal. Still purely local.

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
(Overview first, above the groups; `Finances`: Holdings · Budget · Projections;
`Spending`: Trends · Budget vs Actual · Transactions · Merchants · Categories —
group headings are labels, only the destinations are links). On wide screens (≥`lg`, 1024px) that outline is a
docked left sidebar;
the `☰` collapses it to reclaim horizontal width for the dense sheets. Below the
breakpoint it collapses to a hamburger that opens the same outline as an overlay
drawer, so a narrow header never clips. It is one responsive component (Alpine
state on `<body>`), not two nav systems — which is why it reads consistently
across desktop, mobile portrait, and mobile landscape (where the single top bar
is the big vertical reclaim). Overview (`/s/<snapshot>`) is the landing page
(see [Overview](#overview)); creating, copying, renaming, or switching to a
snapshot lands there. Holdings subsumes the
old standalone Accounts and Assets sheets, which were retired — it is one dense,
spreadsheet-style view over the same tables; information density is a deliberate
feature of these sheets, not a problem to design around. Net worth shows in the
Holdings sheet's footer (the header carries no figures). Holdings' structure,
the grouped-sheet layer it shares with Budget, and the invariants that keep the
sticky chrome correct are documented under [Holdings sheet](#holdings-sheet)
and [Budget sheet](#budget-sheet) below. Import is a header icon; the edit-mode
lock is functional on Holdings, Budget, Transactions, Merchants, and Categories
and muted elsewhere. The old `/s/<snapshot>/status` dashboard was removed; its
URL redirects to Overview.

Two route-scoping/HTMX idioms coexist. Transactions, trends, merchants,
categories, and import use a snapshot-scoped blueprint preprocessor
that places the validated filename and ID in `g`. Holdings, budget, and
projections validate an explicit filename argument. Ledger pages commonly swap
page-specific partials on `HX-Request`; editable sheets swap rows, cells, or
table bodies. One `base.html` provides Tailwind, HTMX, Alpine.js, navigation,
and theme behavior.

### Holdings sheet

Holdings is one table split into four **group-based** bands, rendered top to
bottom: **Cash · Credit Cards · Loans · Assets**. Each band maps directly to a
`holdings.group_key` and its detail table — Cash and Credit Cards come from
`get_accounts`, Loans and Assets from `get_asset_entries`. Every band draws
from exactly one source now (loans no longer straddle two tables), so each is
independently drag-reorderable; debt↔asset equity/LTV pairing
(`calculations.equity_pairs`) is unchanged.

- **Ragged right edge with shared leading columns.** The first four columns —
  Institution·Type·Name·Amount — are shared across all groups and stay aligned
  via `<col>` widths. Each group appends its own trailing columns as real `<td>`
  cells; shorter groups pad with a filler `<td colspan>` to the widest group's
  column count so the HTML table grid stays consistent.
  Cash: Reserve·Funding·As Of. Credit Cards:
  Limit·Available·Used·Rewards·Statement·Due·Linked·As Of. Loans:
  Interest·Mo. Interest·Equity·LTV·Original·Term·Originated·P&I·Paid·Payoff·Due·Linked·As Of. Assets:
  Unit Price·Qty·Source·Est. Return·Mo. Contrib. Equity and LTV show on the
  **loan** row (the debt side of a secured pair). Original, Term, and Originated
  capture loan origination inputs; P&I and Paid are derived amortization values.
  Mo. Interest (owed × rate ÷ 12) and Payoff (when the current balance reaches
  zero at P&I) come from `fintrack/insights/debt.py`, as does a card's Used
  (owed ÷ limit, amber past `card_usage_high_pct`, 30%, in
  `configs/insights.yaml`) — issue #102. Each band's `<tbody>` carries
  `id="holdings-<key>"` so the Overview can link to it.
  Due is a recurring day of month, clamped to month-end when needed.
- **Row accent by group, not sign.** Cash, Credit Cards, Loans, and Assets have
  distinct green, rose, amber, and blue accents. A credit card therefore reads
  as a liability even at a zero balance rather than changing with the current
  amount's sign.
- **Totals.** Each group shows its own subtotal in its heading band; a master
  footer shows **Liquid** and **Net worth** (`calculations.tiered_totals`).
  Liquidity is a cross-cutting property, so it is reported independently of the
  row grouping rather than read off one group's subtotal.
  Liquid also notes its change since the same day last month (clamped to
  month-end), from balance history (`balance_history.balances_on`); accounts
  without a point that old count at today's value, and the tooltip says how many
  had one. No note when no liquid account has a point that old. Net worth gets
  the same note (#107) from every holding's history — loans and assets
  included, a loan's signed point flipped back to the amount owed and an
  asset's point standing in for its whole value — but only once **most** of
  its holdings (more than half) have a point that old, so right after loans
  and assets start their history it doesn't just repeat the Liquid change.
  Both notes show in view and edit mode alike.
- **Stale As Of.** The As Of cell turns amber once a holding's balance is
  older than its group's `stale_days` and red past `very_stale_days`
  (`configs/insights.yaml`: 35 / 95 days for cash and cards, 90 / 180 for loans
  and assets), via `fintrack/insights/stale.py` `staleness()` — the same check
  that puts "last updated N days ago" lines on the Overview (issue #102).
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

**Grouped sheets — shared layer.** Holdings and Budget are both *grouped
sheets*: one `<table class="grouped-sheet">` whose bands are each a
`<tbody class="sheet-group" data-tone="…">` (tinted heading with subtotals, the
band's own sortable column header, data rows, an edit-mode add row), closed by a
pinned footer in a trailing tbody. The pieces are shared, so a third grouped
sheet reuses them rather than copying Holdings:

- markup — `templates/partials/grouped_sheet.html` (macros `group_attrs`,
  `heading`/`subtotal_cell`, `header_row`, `row_class`, `empty_row`,
  `add_row`/`add_button`, `footer`); each page keeps its own cell rendering;
- look — `templates/partials/grouped_sheet.css`, inlined into every page by
  `base.html` (not a `<link>`ed static file: the PR-preview Lambda runs at
  reserved concurrency 1 and 429s parallel subresource requests, which dropped
  the stylesheet): the tone palette (emerald · rose ·
  amber · blue · slate; a band, or a single row, picks one via `data-tone`),
  accent rail, sticky heading/header, sticky edit-mode actions column, footer;
- per-band sorting — `static/js/group-sort.js` (asc → desc → none, state in
  `sort_<band>_col/_dir` URL params, honours a cell's `data-sort-value`;
  a sorted band's drag-reorder is disabled);
- Python — `web/routes/grouped_sheet.py`: `group_columns()` turns a
  `(key, header, right_align, span)` column list into the header metadata, and
  `remap_group_order()` maps a band's local drag permutation onto that band's
  own slots in the full stored ordering (other bands' rows stay put).

**Banding is optional — flat sheets (issue #48).** Transactions, Merchants,
Categories, and Budget vs Actual are *flat* grouped sheets: the same layer with
no bands. `flat_sheet(key, cols, tooltips)` builds the column metadata (no
label, no tone); the page renders `gs.flat_header()` (the shared sortable
header row, in a `<thead>` so it pins to the top with no heading above it), one
`<tbody {{ gs.group_attrs(sheet) }}>` (`sheet-group sheet-group--flat`, no
`data-tone`, so no accent rail), and `gs.footer()` in its own trailing tbody
(footer cells may be `{value, span, extra, title}` dicts so a count label can
span narrow columns). `group-sort.js` sorts the flat body from the `<thead>`
header (URL `sort_<key>_*`), carrying any `data-detail-row` (Budget vs
Actual's link suggestions) with the row above it. A single row may still take a
tone — a flagged possible-duplicate transaction is `data-tone="amber"`, so the
rail marks just that row. The column specs (`TRANSACTIONS_SHEET`,
`MERCHANTS_SHEET`, `CATEGORIES_SHEET`, `ACTUAL_SHEET`) are exposed to templates
as the `flat_sheets` Jinja global, since their row partials are rendered by many
row/cell-swap routes; rows map cells by column key like the banded sheets.
Categories' mutations swap the whole `#categories-table` (so the footer count
stays current), and its actions column exists only in edit mode. Trends keeps
its own table styling (and `sortable.js`), with the Category column pinned for
sideways scrolling. A **Highlight unusual months** toggle (on by default;
`?highlight=off`, kept while paging and switching periods) shades only months
that differ from the category's usual — its median completed month — by more
than 15% and at least $10: red when worse (more spending, or less income), green
when better, in three strengths. Steady bills stay plain, and one-off amounts in
a usually-empty category stand out. Per-month size is left to each row's
expandable bar chart. The month in progress is marked "so far", greyed, never
highlighted, and left out of "usual".

The summary columns follow the same rule (#103, `fintrack/ledger/trends.py`,
shared with `report trends`). **Avg/mo** divides by the *completed months with
data* — months where the snapshot has any transactions in the window, minus the
month in progress — not every calendar month in the window, so a short history
isn't understated and a partial month doesn't drag it down (no completed month
→ "—"). **Change** compares the last completed month to that average.
**Delta** (Avg/mo − Budget/mo, where Budget/mo is annual ÷ 12) is worded by the
row's kind: spending reads "$X over" / "$X under", income and the Net (kept)
row read "$X short" / "$X ahead"; color is green when the result beats the
budget, red when it doesn't.

The footer is the month's **cash flow** (#99, `fintrack/insights/cash_flow.py`
`compute_cash_flow()`, shared with the Overview card, `report trends`, and
`status`): **Income** (the Income category), **Spending** (every other category
except Transfer, as a positive outflow; refunds sit in the category they
reverse, so they lower spending rather than count as income), **Net (kept)**
(income − spending: the sum of every category row, and the row that carries
Budget/mo, Delta, and Change), **Savings rate** (net ÷ income; "—" when income
is zero or negative), and **Budgeted net** (budget income − budget expenses
falling in that month per `budget_entry_in_month`, so an annual bill shows in
its own month; Transfer-category entries left out). Transfer is left out
because both legs of moving money between your own accounts — a card payment
out of checking and onto the card — are Transfer, so only the card purchases
count. Avg/mo for these rows follows the rule above; the average savings rate
is average net ÷ average income, not a mean of monthly rates. The month in
progress shows its figures so far, greyed like the body.

Trends' periods are **Quarterly · Year · Trailing 12 Mo**. **Year** (#102,
replacing Year to Date; old `?period=ytd` links still open it) is the current
year so far, or a full calendar year when paged back, and doubles as the **year
in review** (`fintrack/insights/year_review.py`, shared with `report trends
--year YYYY`). It adds three columns after the months — the year's total, last
year's over the same span, and the change in size (a year in progress is
compared with the same dates last year, Jan 1 – today) — with the cash-flow
footer rows filled in the same way (savings rate compares in points). Above the
table, a panel shows the year's savings rate against last year's, income ·
spending · net, net worth from the start of the year to its end (or today) from
balance history, and the top ten merchants by spending (Income and Transfer left
out; each links to its transactions). Net worth follows the Holdings footer's
rule: a holding with no value before the year counts at its end value, and the
change only shows once most holdings have a starting value.

The actions column (drag + delete) renders only in edit mode. Edits swap the
table's whole body (colgroup included), so a row whose edit changes its band
simply re-renders in the new band.

Two invariants keep the grid sound:

- **Every row spans the full grid** — the same number of column slots in
  every row, actions column included in edit mode. Band headings close with
  their own sticky actions cell (the `heading` macro adds it in edit mode;
  callers size their filler from `max_cols`): heading cells stack above the
  pinned actions column (z-7 vs z-6), so a heading whose last cell spanned into
  the actions slot painted its figures over the drag/delete column when the
  sheet scrolled sideways. `test_every_row_spans_the_full_grid` checks both
  sheets in view and edit mode (and Projections in each view, and the four
  flat sheets in `tests/spending/test_web/test_flat_sheets.py`).
- **Row templates map cells to fields by column key** (the group's
  `col_keys`), never by position, so adding or removing a column can't silently
  shift which field a click edits.

**Sticky spreadsheet chrome — the border invariant.** These sheets use
`border-collapse`, whose borders are painted in the *table's* own layer. When a
row is `position: sticky` — the group heading bands, the column-header rows, the
pinned total rows, and the left accent rail — a collapsed border
scrolls out of view or paints over/beside the sticky cell (reproduced in both
WebKit and Chromium). **So no border on a sticky row may be a border-collapse
border**: draw it as a `box-shadow` on the cell instead (it paints with the
sticky element and stays put). Header underline + inter-column dividers, the
group heading accents, and the row group accents are all
box-shadows (`web/templates/partials/grouped_sheet.css`, `base.html`). Only the total row
stays pinned to the bottom; the add ("+ Add …") row flows inline as the last row
of its group. `web/static/js/sheet-scroll.js` pins the total(s) to the bottom
with a faint grid canvas and drives the four edge scroll-shadows (the side
shadows cover the column header but stop at the group title band).

### Budget sheet

Budget is a grouped sheet with **one band per category** (issue #51). Bands
follow the category taxonomy's `sort_order`; categories an entry uses but the
taxonomy lacks follow alphabetically, and entries with no category close the
sheet as **Uncategorized**. Every band shares one column set —
Category·Description·Amount·Recurrence·When·Remaining·This month·Per month·
Annual·Auto account — so there is no ragged edge. **This month** is what's
scheduled in the current calendar month (an annual bill due in another month is
$0); **Per month** is the annual amount ÷ 12, the same figure as Trends'
Budget/mo (#103). Category stays a column, always shown (as
Holdings keeps Type), because editing it is how an entry moves between bands.
There is **no Kind column**: kind isn't editable on the sheet (the add buttons
set it) and the amount's sign already shows it (expenses are negative). The
CLI's `budget` table keeps its Kind column.

- **Totals.** Each band heading carries its signed (income +, expense −)
  Remaining / This month / Per month / Annual subtotals under those columns; the pinned footer
  is a single **Net** row over every shown entry.
- **Color by category.** Each category gets its own band hue, cycling the
  shared 12-hue palette (`_CATEGORY_TONES`) in category order — the taxonomy,
  then other used categories — so neighbouring bands differ. Colors index the
  full category list, so a category keeps its hue while filters hide its
  neighbours; reordering or adding categories can shift them. Uncategorized is
  slate. Rows carry no tone of their own: the rail follows the band.
- **Add / reorder per band.** A band's add row offers "+ Add expense" and
  "+ Add income" (its majority kind first) and files the stub under that band's
  category. Drag-reorder is per band (`/budget/reorder?category=…`); rows keep
  their full-list position as the edit/delete handle. Kind / Category /
  Recurrence filters work as before, and lock reordering while active.

### Overview

`/s/<snapshot>` (issue #105) is the one page that isn't a spreadsheet: a plain
responsive stack of read-only cards — one column on phones, two on `md`, three
on `xl`. Each card is a headline number, a line or two of context, and a link
to the exact view behind it. It exists so summary information doesn't sit on
top of a sheet, pushing the table down (worst on mobile); the sheets stay dense
tables.

`fintrack/insights/overview.py` `build_overview()` gathers the numbers from the
existing calculations (today: Projections' `timeline.build_timeline()` over the
past 12 months and the next 12, + `summarize()` of its projection),
shared by the web route (`web/routes/overview.py`) and the `status` CLI
command, which prints the same cards as text. It returns the card keys in
order; each key renders `web/templates/overview/cards/<key>.html` with the
`overview/card.html` macros. To add a card, gather its data there, append its
key, add the template, and print a section in `status`. Cards whose feature
hasn't shipped are simply absent.

| Card | Shows | Links to |
|---|---|---|
| Net worth / Liquid | both now; their actual change since the month-end a year back ("since Nov 2025", like for like: summed over the holdings with a value then, `*` when some had none; "No history back to …" when none did); and their projected change by the end of the 12 months | Projections' timeline over that past year and the next (`?months=24&from=YYYY-MM`), or `?months=12` with no history that far back; the whole card is the link |
| Cash flow | this month's savings rate, income, spending, and net so far; the average rate, net, and income over Trailing 12's completed months | Trends (`?period=trailing12`) |
| Debt and cards | total owed on loans, interest this month and total scheduled payments, the next payoff; total card usage (owed ÷ limit, amber past 30%) and owed of limit | Holdings at the Loans band (`#holdings-loan`), or the Credit Cards band without loans |
| Needs attention | how many findings ("All clear" when none), then one line each, first five: recurring charges not in the budget ("Spotify: $11.99/mo, not in budget"), recurring price changes ("Netflix: $15.49 → $17.99 since Oct"), stale balances ("Chase Checking: last updated 41 days ago"), and accounts overdue for a statement ("Chase Checking: no statement imported in 51 days") | what the card leads with: Budget vs Actual's Recurring view filtered to those (`?view=recurring&status=not_budgeted&status=price_change`), or Holdings for stale data |
| Spending alerts | status ("All clear" / how many things to look at), one line per spending alert (five, then "+N more") | Transactions (`?all_months=true&status=flagged`): the flagged charges and this month's rows in hot categories, each dismissable |
| Projection warnings | status ("All clear" / how many accounts dip below their minimum), the low point, each below-minimum account, loan payoffs | Projections opened on the low point's month breakdown (`?months=12&month=YYYY-MM`); below-minimum cells are marked in the grid |

Cards are terse: whole dollars (`fmt_money_whole`), changes as ▲/▼ amounts
(`fmt_change_whole`), one short line per item; exact figures and detail live on
the sheets they link to. Each card is one link (the whole card) to one view;
no per-line links or footer links. The breakdown the warnings card opens still
crowds the grid on phones (#114).

Needs attention is fed by `needs_attention()` in `overview.py`: a list of
`{source, text}` items that each feature appends to: recurring charges
(`source="recurring"`, #100), then stale data (`source="stale"`, #102). Spending
alerts (#101) keep their own card, since they open a different view. Its one
link follows the first item's source.

Stale data (`fintrack/insights/stale.py`, thresholds in
`configs/insights.yaml`) reports, oldest first, each holding whose As Of is past
its group's `stale_days`, then each account that has had confirmed statement
imports but none in `import_stale_days` (35). A holding already reported for a
stale balance isn't reported again for its imports. `status` prints the same
lines.

Debt and cards (`fintrack/insights/debt.py` `debt_and_cards()`) summarizes each
loan — owed, rate, scheduled payment (from the origination fields, as
Projections uses), this month's interest (owed × rate ÷ 12), and the payoff date
at that payment (`amortization.projected_payoff_date`) — plus each card's usage.
A loan without origination fields still counts toward owed and interest but has
no payment or payoff. `status` prints the card as text after Cash flow.

Net worth's "change over the past 12 months" waits on balance history being
recorded over time (#107, #98). An empty
snapshot still renders every card (zeros) under a pointer to Holdings and
Budget.

### Projections sheet

The Projections accounts×months grid is a read-only grouped sheet (issue #52):
the same four Cash · Credit Cards · Loans · Assets bands and tones as Holdings,
always open (no collapse), each with its own sticky heading carrying the band's
per-month subtotals (and Now) and its own column header (Account · months, with
Now before the current month in the Balances view). Sorting is per band
(`group-sort.js`, URL `sort_<group>_*`). Every band header repeats the
projected months' breakdown icons (`sheet.header_link`). Budget flows with no
`auto_account_ref` get a trailing slate **Unassigned** band with one "(no
account)" row; the pinned footer holds Liquid total / Net worth (or their
changes).

Past and future read differently (#98): projected columns are italic on a faint
blue tint, and a 2px rule marks the divider on the Now column (or, in the
Changes view, the current month) when the window holds past months. Both are
drawn per column position with `background-image` from a small `<style>` in
the template, so they layer over every row's own background and never touch the
box-shadows sticky rows draw their edges with. The ◀ window ▶ + Now pager
and the summary line share the pager row above the grid; the 6 / 12 / 24
window size (6M / 1Y / 2Y on phones), Balances / Changes and an Estimate
toggle lead the grid's filter bar (see "Page controls").

## Page controls

Every sheet page puts its controls in the same two places, with one look:

- **Pager row** (`nav.pager_row`): the time pager (◀ label ▶ Latest/Now)
  alone on a row above the sheet, left-aligned, followed only by status for
  that window (Budget vs Actual's badges, Projections' summary). A view
  without a pager keeps the row (Transactions' All time names itself; the
  Recurring view says "Last 13 months") so the sheet doesn't jump. The pager
  never goes in the filter bar.
- **Filter bar** (`sheet_filter_bar.filter_bar`), fused to the sheet's top:
  the page's view toggles first as its `lead` (`nav.segmented`: Month / Year /
  All time, By month / Recurring, Trends' period + Highlight, Projections'
  window + Balances / Changes + Estimate), then the filter dropdowns and
  search, then Reset. Everything is the same compact text-xs control. On a
  phone it stays one row — lead toggles (with `short` labels where needed),
  a "Filters" button that drops the dropdowns into a panel below, and Reset —
  so view toggles never hide behind the Filters button. Trends and
  Projections have lead-only bars (`filters=false`).

## CLI

One Click tree (`fintrack.py`), with global `--db` (default `$FINTRACK_DB`)
and `--snapshot` (defaults to the sole snapshot; required when several exist):

```
snapshots  list|add|copy|rename|delete
accounts   list|add|edit|delete
balance    set <account> <amount> [--date] | history <account>
import     <files…> --account NAME
staging    list | confirm <id> | reject <id>
duplicates list [--all] | mark <txn> | dismiss <txn> | undo <txn> | scan [--apply]
alerts     [list] [--all] | dismiss <txn>|--category NAME | undo <txn>|--category NAME
dedup      [--apply]
transactions link <txn> <entry> | unlink <txn> | suggest | dismiss <txn> <entry> | undismiss <txn> | budget-actual
recurring  [list] [--all] [--dismissed] | add <merchant> | link <merchant> <entry> | dismiss <merchant> | undismiss <merchant>
merchants  list | set
categories list|add|edit|delete
report     monthly | trends [--months N] [--year YYYY]
status | budget | income | expenses | assets | debts | funding   # status = text Overview
project    [--from YYYY-MM] [--months N] [--estimate] [--changes] [--month YYYY-MM]
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
