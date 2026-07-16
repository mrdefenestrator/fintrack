# Spending Tracker — Design Spec

## Summary

A personal spending tracker that ingests credit card and bank statements, classifies transactions via the Claude API, and provides monthly spending visibility through a web UI and CLI. Priorities: accuracy, manual correction capability, minimal data sent externally, and low API costs.

## Interaction Modes

- **Web UI** (Flask + HTMX) — primary interface for browsing, reviewing, correcting, and analyzing transactions
- **CLI** — importing statements, managing accounts and categories, quick status checks
- Both share a common `spending` Python package (repository pattern, SQLAlchemy Core)

## Data Model

Six tables in SQLite, managed by Alembic migrations.

### accounts

Represents a bank or credit card account that statements are imported from.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | auto-increment |
| name | text, unique | e.g. "Chase Visa" |
| institution | text | e.g. "Chase" |
| account_type | text | checking, savings, credit_card |
| created_at | timestamp | |

### imports

One row per imported file. Tracks provenance and import status.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | auto-increment |
| account_id | integer FK → accounts | |
| filename | text | original filename |
| file_hash | text | SHA-256 of file contents, reject exact re-imports |
| imported_at | timestamp | |
| status | text | staging, confirmed, rejected |

### transactions

Raw imported transactions. Immutable after import — corrections are stored separately.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | auto-increment |
| import_id | integer FK → imports | |
| account_id | integer FK → accounts | |
| date | date | transaction date |
| amount | decimal | negative for debits, positive for credits |
| raw_description | text | original description from statement |
| fingerprint | text | hash of (date, amount, description, account, sequence) for dedup |
| created_at | timestamp | |

### merchant_cache

Normalized merchant name to category mappings. Populated by Claude API and manual corrections.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | auto-increment |
| merchant_name | text, unique | normalized merchant name |
| category | text | from the fixed category list |
| source | text | api, manual |
| created_at | timestamp | |
| updated_at | timestamp | |

### transaction_corrections

Per-transaction overrides layered on top of raw data. All fields nullable — only set what you're overriding.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | auto-increment |
| transaction_id | integer FK → transactions, unique | one correction per transaction |
| category | text, nullable | override classification |
| merchant_name | text, nullable | override merchant name |
| notes | text, nullable | |
| created_at | timestamp | |

### categories

The fixed category list, stored in the database (seeded from config).

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | auto-increment |
| name | text, unique | e.g. "Groceries" |
| sort_order | integer | display ordering |

### Resolved Category Logic

Used by all queries:

1. If `transaction_corrections.category` exists for this transaction → use it
2. Else look up the resolved merchant name in `merchant_cache` → use its category
3. Else → "Uncategorized"

### Resolved Merchant Name Logic

1. If `transaction_corrections.merchant_name` exists → use it
2. Else → normalize `transactions.raw_description` via the normalization pipeline

### Derived Transaction Status

Used for filtering in the Transactions tab (not stored — computed from existing data):

- **corrected** — has a row in `transaction_corrections`
- **uncategorized** — resolved category is "Uncategorized" (no merchant cache hit and no correction)
- **categorized** — has a resolved category via merchant cache or correction, and import is confirmed

### Category Validation

`merchant_cache.category` and `transaction_corrections.category` are text fields validated at the application layer against `categories.name`. This avoids FK complexity while ensuring only valid categories are stored.

## Merchant Name Normalization

Raw bank descriptions are cleaned before cache lookup or API classification. The normalization pipeline applies these steps in order:

1. **Uppercase** — normalize case
2. **Strip known prefixes** — payment processor prefixes (SQ *, TST *, PAYPAL *, CKE *, SP *)
3. **Strip trailing reference numbers** — trailing digits, transaction IDs, location codes
4. **Strip trailing location info** — city/state patterns
5. **Collapse whitespace**

Rules are stored in a config file (`configs/normalization.yaml`) so new patterns can be added without code changes.

**Examples:**
- `SQ *COFFEE SHOP 8442 CHICAGO IL` → `COFFEE SHOP`
- `AMZN MKTP US*2K7X9` → `AMZN MKTP`
- `PAYPAL *NETFLIX` → `NETFLIX`

The normalization is intentionally lossy. The original description is always preserved in `transactions.raw_description`. If normalization produces a bad result for a specific merchant, the transaction-level merchant name correction handles it.

## Import & Classification Flow

### CLI Import

```
spending import <files...> [--account "NAME"]
```

Accepts multiple files or directories. Auto-detects format (OFX by extension/magic bytes, CSV by institution config match) and account (from file contents or institution config) where possible.

**Pipeline per file:**
1. Check `file_hash` against `imports` table — reject exact re-imports
2. Detect format and parse into normalized transaction records
3. Generate fingerprints, auto-dedup against existing transactions (sequence-aware hashing handles identical charges on the same day)
4. Flag ambiguous potential duplicates for manual review
5. Insert into `transactions` with a new `imports` row in `staging` status
6. Batch all new unique merchant names (not in `merchant_cache`) and send to Claude Haiku in a single API call
7. Store API results in `merchant_cache` with `source=api`
8. Print summary and link to web staging review

### Web Import

The Import tab provides a drag-and-drop zone / file picker accepting multiple files. After file selection, a form assigns each file to an account (auto-detected where possible, dropdown to override). Submit triggers the same shared pipeline as the CLI. Results land directly in the staging review on the same page.

### Classification API Call

- Send a list of merchant names + the full category list to Claude Haiku
- Only merchant names are sent — no amounts, dates, or account info
- Prompt asks Claude to return a JSON array of `{merchant_name, category}` pairs
- On API failure or timeout: transactions import as "Uncategorized", fixed in review
- The merchant cache means most future imports hit the API only for new merchants

### Staging Review

Imports land in a staging area (import status = `staging`). They are not visible in reports until confirmed.

- Import tab shows pending batches, each expandable to its transactions
- Each transaction shows: date, amount, resolved merchant, assigned category, status (new / duplicate-flagged)
- Actions: inline category correction, merchant name correction, dismiss duplicate flags, bulk actions (re-categorize, approve, reject)
- "Confirm batch" finalizes — sets import status to `confirmed`, transactions appear in reports

## Deduplication Strategy

Sequence-aware fingerprinting:

- Fingerprint = hash of (date, amount, raw_description, account_id, sequence_number)
- When multiple transactions within the same statement share the same (date, amount, raw_description), they get incrementing sequence numbers
- On import, transactions whose fingerprint matches an existing confirmed transaction are auto-skipped
- When fingerprint counts differ between overlapping imports (ambiguous case), transactions are flagged in the staging area for manual review

## Manual Corrections

Two levels, stored in the override layer (original import data is never modified):

### Merchant-Level

Stored in `merchant_cache`. When you correct a transaction's category in the UI, you're prompted: "Apply to all transactions from this merchant?" If yes, the merchant cache entry is updated (or created) with `source=manual`. All past and future transactions from that merchant reflect the change.

### Transaction-Level

Stored in `transaction_corrections`. One-off overrides for a specific transaction's category or merchant name. Takes precedence over the merchant cache for that transaction only.

### Merchant Management View

A dedicated Merchants tab shows all merchant-to-category mappings: merchant name, category, source (api/manual), transaction count, last seen date. Supports inline category editing, text search, and filtering by category or source.

## Web UI

### Navigation

Five tabs, HTMX-swapped content. Tab order: Monthly (default landing) → Transactions → Trends → Merchants → Import.

### Monthly Tab (Default)

- Month picker at top (prev/next arrows + dropdown)
- Category breakdown table: category name, transaction count, this month's total, trailing 3-month rolling average, indicator of above/below average
- Click a category row to expand and show its transactions inline (HTMX swap)
- Grand total row at bottom

### Transactions Tab

- Sortable table: date, merchant, raw description, category, amount, account, status (categorized/uncategorized/corrected)
- Filter bar: account dropdown, category dropdown, month picker, text search, status filter
- Bulk actions: select rows via checkboxes → re-categorize
- Click a category to inline-edit (prompts for merchant-level correction)
- Click a merchant name to inline-edit (transaction-level correction)

### Trends Tab

- Preset period selector: Quarterly, Year-to-Date, Trailing 12 Months, Annual
- Category table: category name, total for period, monthly average for period, sparkline showing monthly values across the period
- Grand total row

### Merchants Tab

- Table: merchant name, assigned category, source (api/manual), transaction count, last seen date
- Inline category editing (merchant-level correction)
- Text search, filter by category or source

### Import Tab

- Drag-and-drop zone / file picker (multiple files)
- Account assignment form per file (auto-detect with dropdown override)
- Pending staging batches list, expandable to transaction review
- Per-batch: confirm, reject
- Per-transaction: inline corrections, duplicate flag resolution, bulk actions

## CLI Commands

```
spending import <files...> [--account "NAME"]
```
Import statements. Accepts files or directories, auto-detects format and account.

```
spending accounts list
spending accounts add --name "Chase Visa" --institution "Chase" --type credit_card
spending accounts edit <id> --name "..."
spending accounts delete <id>
```
Manage accounts.

```
spending categories list
spending categories add --name "Groceries" --sort-order 5
spending categories edit <id> --name "..."
spending categories delete <id>
```
Manage the category list.

```
spending status
```
Quick summary: current month spending total, top 5 categories, unreviewed transaction count, pending staging batches.

```
spending serve
```
Start the Flask web server.

## Input Formats

### OFX/QFX

Parsed with `ofxparse`. Standardized format — one parser handles all institutions. Account info often extractable from file contents.

### CSV

Per-institution config files in `configs/institutions/`. Each config specifies column mappings (date, amount, description), date format, and optionally the account name. CSV files are matched to an institution config via the `--account` flag (the account's institution determines which config to use) or by matching CSV header columns against the configs. New institution = new config file, no code change.

### PDF

Out of scope for v1. Can be added later if a bank forces it.

## Category Taxonomy

A fixed flat list stored in the `categories` table, seeded from `configs/categories.yaml`. Categories can be managed via CLI (`spending categories add/edit/delete`) or directly in the config.

The fixed list is sent to Claude in the classification prompt, constraining responses to valid categories only.

## Time Views & Aggregation

- **Monthly** (primary): category totals for a single month, with trailing 3-month rolling averages for comparison
- **Preset periods**: Quarterly, Year-to-Date, Trailing 12 Months, Annual — each shows category totals, monthly average, and sparklines of monthly values
- Sparklines are rendered inline in tables (lightweight library or inline SVGs)

## Project Structure

```
spending/
├── spending/              # shared package
│   ├── __init__.py
│   ├── cli.py             # click CLI entrypoint
│   ├── db.py              # SQLAlchemy engine, session setup
│   ├── models.py          # table definitions (SQLAlchemy Core metadata)
│   ├── repository.py      # domain queries
│   ├── importer/
│   │   ├── __init__.py
│   │   ├── ofx.py         # OFX/QFX parser
│   │   ├── csv.py         # CSV parser with per-institution configs
│   │   ├── normalize.py   # merchant name normalization pipeline
│   │   └── dedup.py       # fingerprinting and deduplication
│   ├── classifier.py      # Claude API classification + cache logic
│   └── types.py           # shared TypedDicts, enums
├── web/
│   ├── __init__.py
│   ├── app.py             # Flask app factory
│   ├── routes/            # route blueprints per tab
│   ├── static/            # CSS, JS (HTMX, sparkline lib)
│   └── templates/         # Jinja2 templates
├── tests/
│   ├── test_repository.py
│   ├── test_importer/
│   ├── test_classifier.py
│   └── e2e/               # Playwright browser tests
├── configs/
│   ├── categories.yaml    # default category list
│   ├── institutions/      # per-institution CSV parser configs
│   └── normalization.yaml # merchant name normalization rules
├── migrations/            # Alembic migration scripts
├── alembic.ini
├── spending.py            # CLI entrypoint
├── pyproject.toml
├── mise.toml
├── Dockerfile
├── docker-compose.yml
├── CLAUDE.md
└── DESIGN.md
```

## Dependencies

**Runtime:**
- Flask
- SQLAlchemy
- Alembic
- ofxparse
- anthropic
- click

**Dev:**
- pytest, pytest-cov
- ruff
- playwright, pytest-playwright

## Tooling

Mirroring the `finances` reference project:
- Python 3.12 via `uv` (managed by `mise`)
- `mise.toml` for task running (setup, format, lint, test, serve)
- `ruff` for formatting (88-char line length) and linting (E501 ignored)
- Multi-stage Dockerfile (uv builder → slim runtime)
- `docker-compose.yml` for deployment

## Privacy

Only merchant names are sent to the Claude API. No amounts, dates, account numbers, or personal information. The merchant cache means the vast majority of transactions are classified locally after the first few imports. Raw descriptions stay local in the SQLite database.

## Out of Scope (v1)

- PDF statement parsing
- Multi-currency
- Multi-user / household sharing
- Budgeting / spending targets
- Bank API integrations (Plaid, etc.)
- Local/offline LLM classification (Ollama)
- Custom date range queries (use preset periods)
