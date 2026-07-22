# CLAUDE.md

## Project Overview

Personal finance tracker merging a transaction ledger (statement import,
Claude-based merchant classification, spending trends) with net-worth and
cash-flow tracking (accounts, budget entries, assets/debts, projections).
Everything is scoped to snapshots (independent households). Has a Click CLI
and a Flask web UI. See DESIGN.md for the full architecture and data model.

## Tech Stack

- Python 3.12, managed via `uv` (pyproject.toml, `uv sync`), installed via `mise`
- Flask + HTMX + Alpine.js web UI (port 5003)
- SQLite via SQLAlchemy Core (not ORM); Alembic for migrations
- Claude API (Haiku) for merchant classification
- Click for the CLI; Playwright for e2e tests

## Common Commands

```bash
# Setup
mise run setup             # uv sync into .venv
mise run playwright-install  # one-time, for e2e tests

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

- `fintrack/core/` — engine/FK pragma, unified models (single MetaData), config paths, formatting
- `fintrack/ledger/` — statement importer (OFX/CSV, normalization, dedup), classifier, ledger repositories
- `fintrack/accounts/` — unified accounts repository, balance_history, OFX account matching
- `fintrack/budget/` — budget entries, recurrence/proration engine
- `fintrack/networth/` — assets/debts, key-number and funding calculations, liquidity-tier totals + equity pairs
- `fintrack/projections/` — multi-month balance projection engine + estimators
- `fintrack/snapshots/`, `fintrack/migrate/` (legacy one-time import), `fintrack/cli/` (one module per command group)
- `web/` — Flask app, routes/, Jinja2/Tailwind/HTMX templates
- `tests/` — unit tests by domain; `tests/e2e/` Playwright (marker `e2e`)
- `configs/` — categories.yaml, normalization.yaml, institutions/
- `migrations/` — Alembic (single chain)
- `fintrack.py` — CLI entrypoint

## Code Style

- **Ruff** for formatting (88-char lines) and linting (E501 ignored)
- Modern Python: type hints, f-strings, TypedDicts
- Repository pattern: all DB queries live in repository modules — routes and
  CLI never build SQL
- SQLAlchemy Core query building, real `Date`/`Numeric` column types

## Key Architecture Decisions

- Snapshots scope all household data (accounts directly; imports/transactions/
  balance_history via account). merchant_cache, categories, and corrections
  are deliberately global.
- **Privacy constraint: only normalized merchant names (plus the category
  list) are ever sent to the Claude API** — never amounts, dates, account
  numbers, or raw statement text. Do not widen the classifier prompt.
- Raw imported data is immutable; user fixes live in the
  transaction_corrections overlay.
- Imports stage until confirmed; new merchants are classified at import time,
  and confirming records statement balances into balance_history.
- `accounts.balance` is the canonical signed balance (negative = owed on CCs);
  it is a denormalized cache of the latest balance_history point — always
  write through `record_balance()`, never update the column directly.
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
  be prudent about what data is *relevant*, not about decluttering.
- **Holdings direction.** Holdings unifies the Accounts and Assets sheets into
  one spreadsheet, split into four **type-based** groups — Cash · Credit Cards ·
  Loans · Assets — each with its own tight column set (a group only shows the
  columns that apply to it; blank slots pad to align As Of). Grouping is a
  display concern over the same two tables (`accounts`; `asset_entries` by
  `kind`) — no data migration. The standalone Accounts and Assets **web pages**
  have been retired; Holdings is the finances landing page. See DESIGN.md
  "Holdings sheet" for the columns, the Liquid/Net worth footer, computed CC
  Available, per-group reorder, and the **sticky-row border invariant** (sticky
  rows must use box-shadow, never border-collapse borders). The web GUI (Holdings)
  and the Python CLI (which keeps separate `accounts`/`assets`/`debts` commands)
  are both first-class; keep them at parity.
- Core product goals the UI serves: current liquid holdings, net worth,
  budgeting, spending tracking, deviation from budget, and (eventually)
  net-worth projections.
- Sequence-aware fingerprinting for transaction dedup.
- SQLite runs with `foreign_keys=ON`; snapshot FKs cascade, ownership refs
  (payment_account_ref, auto_account_ref, asset_ref) do not.
- Legacy spending/finances data enters only via `fintrack migrate-legacy`,
  never via Alembic upgrade of an old DB.

## Testing Gotchas

- E2e tests point `ANTHROPIC_BASE_URL` at a closed port — keep it that way so
  tests can never call the real API.
- Don't use generic `button[type=submit]` selectors in e2e tests: the header
  edit-mode toggle is a submit button.
- Killing `uv run` can orphan its python child — check for stale servers on
  port 5003 if e2e behavior looks cached.
