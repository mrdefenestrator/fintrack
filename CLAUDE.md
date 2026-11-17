# CLAUDE.md

## Project Overview

Personal finance tracker merging a transaction ledger (statement import,
Claude-based merchant classification, spending trends) with net-worth and
cash-flow tracking (accounts, budget entries, assets/debts, projections).
Everything is scoped to snapshots (independent households). Has a Click CLI
and a Flask web UI. See DESIGN.md for the full architecture and data model.

## Tech Stack

- Python 3.12, managed via `uv` (pyproject.toml, `uv sync`), installed via `mise`
- Flask + HTMX + Alpine.js web UI (port 5003), styled with Tailwind CSS v3
- Node 22 (via `mise`) at **build time only**: `package.json` pins Tailwind,
  Alpine, htmx and Sortable; `npm run build` compiles the CSS and copies the JS
  into `web/static/dist/` (gitignored). No CDNs — the app works offline.
- SQLite via SQLAlchemy Core (not ORM); Alembic for migrations
- Claude API (Haiku) for merchant classification
- Click for the CLI; Playwright for e2e tests

## Common Commands

```bash
# Setup
mise run setup             # uv sync + npm ci + build front-end assets
mise run playwright-install  # one-time, for e2e tests

# Front-end assets (serve/test/test-e2e run `assets` automatically)
mise run assets            # npm run build → web/static/dist/
mise run assets-watch      # rebuild Tailwind CSS as templates change

# Tests
mise run test              # all CI checks: format check, lint, unit, e2e
mise run test-unit         # pytest unit tests with coverage
mise run test-e2e          # Playwright e2e tests

# Format / Lint
mise run format
mise run lint

# CLI
uv run python fintrack.py --help
uv run python fintrack.py --snapshot NAME status
uv run python fintrack.py import ./statements/ --account "Chase Checking"

# Web UI (http://localhost:5003)
mise run serve

# Migrations
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"
```

Environment: `FINTRACK_DB` (SQLite path, default `fintrack.db`),
`FINTRACK_PORT` (default 5003), `ANTHROPIC_API_KEY` (optional — classification
degrades to a warning without it).

## Project Structure

- `fintrack/core/` — engine/FK pragma, unified models (single MetaData), the holdings supertype/subtype CRUD helper (`holdings.py`), config paths, formatting
- `fintrack/ledger/` — statement importer (OFX/CSV, normalization, dedup, near-duplicate matching), duplicate scan, spending-alert checks, classifier, ledger repositories
- `fintrack/accounts/` — unified accounts repository, balance_history, OFX account matching
- `fintrack/budget/` — budget entries, recurrence/proration engine
- `fintrack/networth/` — assets/debts, key-number and funding calculations, liquidity-tier totals + equity pairs
- `fintrack/projections/` — multi-month balance projection engine + estimators
- `fintrack/insights/` — Overview numbers (web landing page + `status`), cash flow, spending alerts, stale data, debt and cards, year in review, and their thresholds (`settings.py`)
- `fintrack/snapshots/`, `fintrack/migrate/` (legacy one-time import), `fintrack/cli/` (one module per command group)
- `web/` — Flask app, routes/, Jinja2/Tailwind/HTMX templates; `web/static/src/`
  is the Tailwind input, `web/static/dist/` the built (gitignored) assets
- `package.json`, `tailwind.config.js`, `scripts/copy-vendor.mjs` — front-end build
- `tests/` — unit tests by domain; `tests/e2e/` Playwright (marker `e2e`)
- `configs/` — categories.yaml, normalization.yaml, alerts.yaml, insights.yaml, institutions/
- `migrations/` — Alembic (single chain)
- `fintrack.py` — CLI entrypoint

## Code Style

- **Ruff** for formatting (88-char lines) and linting (E501 ignored)
- Modern Python: type hints, f-strings, TypedDicts
- Repository pattern: all DB queries live in repository modules — routes and
  CLI never build SQL
- SQLAlchemy Core query building, real `Date`/`Numeric` column types
- Tailwind class names must be complete literal strings — in templates, JS, or
  Python (routes return class strings, so `web/**/*.py` is scanned). The
  compiled CSS only contains classes the scanner sees; never assemble one from
  pieces (`'bg-' + color`, `f"text-{tone}-600"`).
- Front-end library upgrades come through Dependabot (`npm` ecosystem); a
  manual one is `npm install <pkg>@<ver>` then `mise run test`. Tailwind stays
  on v3 (v4 is a separate migration).
- Pin every version exactly — no `latest`, no bare majors: tools in
  `mise.toml`, Docker base images (by full tag), CI runners (`ubuntu-24.04`).
  Dependabot bumps the Dockerfiles but not `mise.toml`, so bump uv and Node
  there by hand, in step with the Dockerfiles.

## Sessions

- When a session starts work on a GitHub issue, rename the session right away
  to `#<issue> <short description>` (about 50 characters, the issue title
  shortened), e.g. `#69 Self-host front-end assets`. Several issues: list each
  number first (`#69 #92 Preview static files`). No issue: just the
  description. Rename again if the work moves to a different issue.

## Pull Requests

- Every PR description names each issue it resolves with a closing keyword,
  one per line (`Closes #69`, then `Closes #92`), so GitHub closes them on
  merge. The keyword must come before **each** number — `Closes #69, #92`
  closes only #69 — and it must be in the description (a title like
  "Fix footer (#92)" closes nothing). Issues a PR only touches get a plain
  `Related: #N` instead.

## Key Architecture Decisions

- Snapshots scope all household data (accounts directly; imports/transactions/
  balance_history via account). Categories and corrections are deliberately
  global. merchant_cache rows are global, but the web UI and CLI filter the
  merchants list by snapshot (only merchants with transactions in the
  snapshot's accounts are shown).
- **Privacy constraint: only normalized merchant names (plus the category
  list) are ever sent to the Claude API** — never amounts, dates, account
  numbers, or raw statement text. Do not widen the classifier prompt.
- Raw imported data is immutable; user fixes live in the
  transaction_corrections overlay.
- A transaction can be linked to the budget entry it realizes via
  `budget_entry_ref` on the corrections overlay (one transaction → at most one
  entry; one entry → many transactions; the occurrence is derived from the
  transaction date). The link powers per-entry budget-vs-actual, missed-charge
  and price-drift detection, and current-month projection true-up
  (`fintrack/budget/reconcile.py`, DESIGN.md "Transaction–budget association").
  Suggestions are a local heuristic only — **do not send transaction data to the
  Claude API** for matching; the classifier privacy constraint still holds.
  Cross-snapshot links are blocked in the repository, not the schema
  (corrections carry no snapshot_id). A linked entry's category **pins** the
  transaction's resolved category (`coalesce(budget_entry.category,
  correction.category, merchant_cache.category, 'Uncategorized')`), so the
  Category and Budget columns can't contradict; the Transactions sheet shows
  that category read-only while linked. Budget-vs-actual lives **on the Budget
  sheet** (#143: Actual · Delta · Status per entry for the viewed month, link
  suggestions in edit mode; the old `/budget/actual` page redirects there), and
  the CLI `budget` table shows the same columns — keep the two at parity.
- **Budget history** (#136, DESIGN.md "Budget history"): an entry is planned
  from `start_month` through `end_month` (NULL = unbounded) and its amount/
  schedule are versioned (`budget_entry_versions`; the entry's own schedule
  columns are the base version). Reads take an `as_of` month; the recurrence
  engine resolves each month's version itself, so per-month readers need
  nothing extra, and one-month listings filter with `entry_active`. Edits made
  while viewing a month apply from it until the next change
  (`update_budget_entry(effective=…)`); description/category/account apply to
  every month. Stop a line with `end_budget_entry`, not delete (delete loses
  its history and unlinks its transactions). The Budget sheet shows one month
  (`?ym=`); its URLs carry `ym` via a `url_defaults` hook — keep new sheet
  endpoints in `_SHEET_ENDPOINTS`.
- Recurring charges (`fintrack/budget/recurring.py`, DESIGN.md "Recurring
  charges") detect steady merchant series locally and match them to the
  budget; findings show on the Budget sheet's Recurring view
  (`/budget?view=recurring`), the Overview "Needs attention" card, and `fintrack
  recurring`. The detected series also sharpen `suggest_links` scores
  (`recurring.series_index`, #119). Same rule: **never send transaction data
  to the Claude API**.
  Needs attention is a list (`overview.needs_attention()`) that other findings
  opening the same kind of view (e.g. #102's) can append `{source, text}`
  items to; Spending alerts (#101) keep their own card.
- Imports stage until confirmed; new merchants are classified at import time,
  and confirming records statement balances into balance_history.
- Holdings are a supertype/subtype split: one slim `holdings` spine
  (`group_key` = cash/credit_card/loan/asset, `type`, `name`, institution,
  as_of, per-group sort_order) plus four detail tables
  (`cash_details`/`credit_card_details`/`loan_details`/`asset_details`).
  Composite FKs make wrong-group detail rows, cross-snapshot refs, and
  retyping an imported holding out of the importable groups unrepresentable.
  The repositories keep a **frozen dict API** (`Account`/`AssetEntry`
  TypedDicts) over this via `fintrack/core/holdings.py`; routes, CLI, and
  calculations never see the two-level tables. See DESIGN.md "Data model".
- A holding's detail `balance` is the canonical signed balance (negative =
  owed on CCs and loans); it is a denormalized cache of the latest
  balance_history point — always write through `record_balance()`, never
  update the column directly. A credit card's `available` is **computed**
  (`credit_limit + balance`), never stored. Loans and assets keep dated
  values there too (an asset's point is its value; price refreshes add
  `'price'` points), so `balances_on()` gives any holding's value on a past
  date — see DESIGN.md "Balance history".
- A loan is its own group (`loan_details`), unifying what used to be
  account-type loans and debt entries; loans are created only as debt
  entries (`type` fixed to `loan`), never as accounts, and carry the
  origination/amortization fields. Loan Due is a recurring day of month
  (1–31), matching account Due; nonexistent days clamp to month-end.
- Liquidity tier (liquid/semi-liquid/illiquid) is fixed by holding **type**
  with no per-holding override; the type→tier maps in `fintrack/core/types.py`
  are the single source of truth (add new types there, not ad-hoc). Tier
  totals nest: liquid ⊂ investable ⊂ net worth (`calculations.tiered_totals`).
- **Spreadsheet-first UI.** The finances sheets (accounts, budget, assets, and
  the unified holdings view) are intentionally dense, wide, editable tables —
  the spreadsheet model is a feature, not a problem to design around. Columns
  exist to expose known data for entry, editing, and checking completeness/
  consistency, so favor showing fields over hiding them behind expand/detail
  drawers. Information density is not a concern and horizontal scroll is fine;
  be prudent about what data is *relevant*, not about decluttering. In edit
  mode every field that can't be edited is greyed (`sheet.readonly()`, #147),
  on every sheet alike — give any new read-only cell the same treatment.
- **Holdings direction.** Holdings unifies the Accounts and Assets sheets into
  one spreadsheet, split into four **group-based** bands — Cash · Credit Cards ·
  Loans · Assets — using a shared Institution·Type·Name·Amount·Details·Due·
  Linked·As Of spine (#142), so Due/Linked/As Of line up across bands ("-"
  where they don't apply). Details is a **compound column**
  (`grouped_sheet.compound_column()`): the band's own fields on one line of a
  CSS grid, labeled in the band's header on the same template, with track
  widths sized server-side from the values shown (`fit_track_widths()`; the
  rows' grids are independent, so CSS can't). Its fields stay keyed and
  individually click-to-edit
  (`data-cell-key="f-<field>"` for cell-nav), and each label sorts the band by
  its field (`f-<field>`; the Details cell itself doesn't sort). Change a
  band's fields in `_DETAILS` (`web/routes/holdings.py`). Each band maps to one
  `holdings.group_key` and its detail table (Cash/Credit from `get_accounts`,
  Loans/Assets from `get_asset_entries`); no band mixes source tables, so each
  reorders independently. The standalone Accounts and Assets **web pages**
  have been retired; Overview is the landing page. See DESIGN.md
  "Holdings sheet" for the columns, the Liquid/Net worth footer, computed CC
  Available, per-group reorder, and the **sticky-row border invariant** (sticky
  rows must use box-shadow, never border-collapse borders). The web GUI (Holdings)
  and the Python CLI (which keeps separate `accounts`/`assets`/`debts` commands)
  are both first-class; keep them at parity.
- **Grouped sheets.** Holdings, Budget (one band per category, colored per
  category, with This month/Actual/Delta/Per month/Annual subtotals and a Net footer; no Kind
  column — the sign shows it), and the read-only Projections grid (Holdings'
  bands + an Unassigned band) share one grouped-sheet layer —
  `partials/grouped_sheet.html` macros, `partials/grouped_sheet.css` (inlined
  in `base.html`), `static/js/group-sort.js`, `web/routes/grouped_sheet.py`. Build new
  banded sheets on it rather than copying a page's markup/CSS. Two invariants:
  **every row spans the full grid** — band headings included, which close with
  their own sticky actions cell in edit mode, or heading content paints over
  the pinned actions column (`test_every_row_spans_the_full_grid`); and row
  templates map cells to fields **by column key, never position**, so adding or
  dropping a column can't shift which field a click edits. Banding is
  optional: Transactions, Merchants, Categories, and Budget's Recurring view are
  **flat** grouped sheets (`flat_sheet()`, `gs.flat_header`, one
  `sheet-group--flat` body, `gs.footer`) — build any new plain sheet the same
  way rather than on bare `sortable.js` tables (Trends alone keeps its own
  styling). Budget, Transactions, Merchants, and Projections also take a
  **Group by** choice (#137: `GroupBy` options + `selected_group()` +
  `category_bands()` in `grouped_sheet.py`, `?group=`, the `group_by_select`
  dropdown in the filter bar); Holdings stays fixed-group since its bands'
  columns differ. Holdings and Projections share the Type / Balance /
  Institution filters (`web/routes/holding_filters.py`, #144). An edit that moves rows between bands or changes many rows
  re-renders the table into its scroll container (`HX-Retarget`), not a full
  reload, so the scroll position survives. See DESIGN.md "Holdings sheet" / "Budget
  sheet" / "Selectable grouping".
- **Row actions** (#148, DESIGN.md "Row actions"): every sheet's edit-mode
  actions column is the same two slots — grip (drag, or focus + ↑/↓; blank
  where rows don't reorder) and a ⋯ menu of labeled items, destructive last,
  confirming in place. Build any new row action as a menu item via
  `partials/row_actions.html` (`ra.cell` / `ra.item` / `ra.delete_item`),
  never as another icon in the cell; behavior lives in
  `static/js/row-actions.js`. In e2e tests open a row's menu with
  `tests/e2e/controls.open_row_menu(row)` and pick items by data attribute.
- **Page controls** (DESIGN.md "Page controls"): the time pager sits alone on
  the pager row above a sheet (`nav.pager_row`), never in the filter bar;
  every other control (view toggles via `nav.segmented`, filters, Reset) goes
  in the sheet's filter bar — toggles as its always-visible `lead`. On a phone
  the bar is one row (`tests/e2e/controls.py` checks it).
- **Phones** (#146, DESIGN.md "Phones"): a phone is narrow **or short** (a
  landscape phone). Use the `phone:` / `wide:` Tailwind screens, not `sm:`,
  for anything that should switch on phones, so landscape gets it too; the
  same queries are repeated in hand-written CSS — keep them in step. Phones
  get 24px sheet rows (`--row-h`; never hard-code a row height). When
  there's a bottom safe-area inset, sheets run to the bottom edge with the
  last total row padded by it (`--sheet-inset-b`); with none (a Safari tab)
  they keep their gutter, since iOS 26 tints its toolbar from whatever
  touches the edge. On touch screens the sheet scroller is isolated so
  iOS's scroll indicator stays above the sticky rows.
- **Overview is the landing page** (`/s/<snapshot>`, first nav item): a
  plain responsive stack of read-only cards, the one page that isn't a
  spreadsheet. Summary numbers go here, not on top of a sheet.
  `fintrack/insights/overview.py` `build_overview()` feeds both the page and
  the `status` CLI command (its text version); add a card by appending its key
  there plus `web/templates/overview/cards/<key>.html`. Projections keeps only a
  one-line summary above its grid. See DESIGN.md "Overview".
- Cash flow (income, spending, net kept, savings rate, budgeted net) has one
  calculation, `fintrack/insights/cash_flow.py`, used by the Trends footer, the
  Overview card, `report trends`, and `status`. Income is the Income category;
  spending is every other category except Transfer (refunds lower spending).
- Core product goals the UI serves: current liquid holdings, net worth,
  budgeting, spending tracking, deviation from budget, and (eventually)
  net-worth projections.
- Duplicate detection is layered (DESIGN.md "Duplicate detection"): bank
  transaction id (OFX FITID, `transactions.external_id`) and the
  sequence-aware fingerprint skip exact copies; near-matches (same amount,
  ±3 days, similar merchant) are only ever **flagged** in the
  `transaction_duplicates` overlay for the user to resolve. A row resolved as
  `duplicate` is hidden from every confirmed-transaction read via
  `not_hidden_duplicate()` — apply it in any new query over confirmed
  transactions (or build on `base_transaction_query`). Matching is local;
  never send transaction data to the Claude API for it.
- Spending alerts (DESIGN.md "Spending alerts") — large charge, new merchant,
  category running hot — are pure, local checks in `fintrack/ledger/alerts.py`
  with thresholds in `configs/alerts.yaml`; `fintrack/insights/alerts.py`
  `get_alerts()` feeds the Overview "Spending alerts" card, the Transactions
  markers + "Flagged" filter, the import confirmation, `fintrack alerts`, and
  `status`. Only dismissals are stored (`alert_dismissals`). Never send
  transaction data to the Claude API for them.
- Insight add-ons (#102, DESIGN.md "Overview" / "Holdings sheet" / Trends):
  stale data (`fintrack/insights/stale.py`) appends `source="stale"` items to
  Needs attention and colors Holdings' As Of per group; debt and cards
  (`insights/debt.py`) feeds its Overview card plus Holdings' Mo. Interest,
  Payoff, and Used columns; year in review (`insights/year_review.py`) backs
  Trends' Year period and `report trends --year`. Every threshold lives in
  `configs/insights.yaml` (`InsightsConfig`; unknown keys are an error), so
  tune there rather than in code. All local; nothing goes to the Claude API.
- SQLite runs with `foreign_keys=ON`; snapshot FKs cascade, ownership refs
  (payment_account_ref, auto_account_ref, secured_asset_ref) do not. Those
  refs are composite (`(ref, snapshot_id)`), so they can't cross snapshots.
- Legacy spending/finances data enters only via `fintrack migrate-legacy`,
  never via Alembic upgrade of an old DB.

## Testing Gotchas

- E2e tests point `ANTHROPIC_BASE_URL` at a closed port — keep it that way so
  tests can never call the real API.
- Don't use generic `button[type=submit]` selectors in e2e tests: the header
  edit-mode toggle is a submit button.
- Killing `uv run` can orphan its python child — check for stale servers on
  port 5003 if e2e behavior looks cached.
- E2e runs offline: `tests/e2e/conftest.py` aborts any request to a non-local
  host and fails the test, so never add a CDN `<script>`/`<link>`. If pages
  render unstyled, the assets weren't built — run `mise run assets` (the app
  also logs a warning at startup when `web/static/dist/tailwind.css` is missing).
- **The PR preview serves `/static` from a second function.** The app Lambda
  runs at reserved concurrency 1 (one seeded SQLite DB in `/tmp`) and answers
  concurrent requests with 429 instead of queueing; browsers don't retry
  subresources. So `pr-preview.yml` also deploys the same image as
  `fintrack-pr-N-static` (`FINTRACK_STATIC_ONLY=1`: serves only `/static`, no
  DB, many copies — capped at 20 to bound cost), and the app's `FINTRACK_STATIC_URL` points every
  `url_for('static', …)` at it (`FintrackFlask` in `web/app.py`; the manifest
  stays on the app's origin). Static files can live in their own files again.
  htmx requests still go to the app function: one that gets a 429 is retried
  (`base.html`); if one still fails (or the server errors), a filter checkbox
  reverts to what the server rendered and a short "Couldn't reach the server"
  notice shows. `tests/e2e/networth/test_static_host.py` reproduces the 429s
  by having Playwright fulfil the app host's `**/static/**` with status 429.
- **Use the dense seed for visual checks.** The e2e fixture is too small to
  scroll. `FINTRACK_DB=x.db uv run python scripts/seed_example.py` seeds
  **Dense Household** (overflows every sheet; what the preview shows), plus
  Sparse and Empty households.
- **Verify UI refactors by screenshot diff against `main`.** Check out `main`
  in a `git worktree`, serve it and the branch side by side on copies of the
  same seeded DB, and screenshot each sheet at top / mid / bottom vertical
  scroll and nudged off the left and right edges (narrow the viewport until it
  scrolls sideways), in view and edit mode (and dark). Pixel-diff the pairs —
  identical means no visual regression.
- Servers started by hand with `FLASK_DEBUG=0` (as e2e does) cache Jinja
  templates — restart after editing a template. `mise run serve` runs in debug
  and auto-reloads.
- On grouped sheets, `tr:has-text('Groceries')` also matches the band heading
  row (its label is the category). Target data rows with
  `tr[data-reorder-index]` or the row's `id` (`budget-row-N`, `holding-row-…`).
- Cloud sandbox only: the project's pinned Playwright may expect a different
  Chromium build than the one preinstalled under `/opt/pw-browsers`. Point
  `PLAYWRIGHT_BROWSERS_PATH` at a scratch dir containing the path it asks for
  (e.g. `chromium_headless_shell-<ver>/chrome-linux/headless_shell`) symlinked
  to the installed `chrome-headless-shell` binary; don't run
  `playwright install`.
- Find stale dev servers with `pgrep -f "python -m web[.]app"` — a plain
  `pkill -f web.app` also matches (and kills) the shell running it.
