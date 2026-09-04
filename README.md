# fintrack

Personal finance tracker covering both halves of the picture in one app:

- **Ledger** — import bank/credit-card statements (OFX/QFX/CSV) into a staging
  area, dedup against prior imports, classify merchants into categories via the
  Claude API, correct anything, and analyze monthly spending trends.
- **Net worth & cash flow** — accounts with balance history, scheduled budget
  entries with recurrence, assets and debts, funding analysis, and multi-month
  balance projections.

Everything is scoped to a **snapshot** (an independent household): accounts,
imports, transactions, budget entries, and assets. Classification knowledge
(merchant → category cache and the category taxonomy) is shared across
snapshots.

fintrack is the merge of two predecessor apps, **spending** and **finances**;
both git histories are preserved in this repo. See [DESIGN.md](DESIGN.md) for
the architecture and data model.

## Setup

Tooling is managed by [mise](https://mise.jdx.dev/) and
[uv](https://docs.astral.sh/uv/):

```bash
mise run setup                # uv sync — installs everything into .venv
mise run test                 # format check, lint, unit tests, e2e tests
mise run playwright-install   # one-time: browser binaries for e2e tests
```

Configuration is via environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `FINTRACK_DB` | `fintrack.db` in the repo root | SQLite database path |
| `FINTRACK_PORT` | `5003` | Web server port |
| `ANTHROPIC_API_KEY` | — | Enables merchant classification (optional; imports work without it, classification is skipped with a warning) |

**Privacy:** only normalized merchant names are ever sent to the Claude API —
never amounts, dates, account details, or raw statement text.

## Web UI

```bash
mise run serve    # http://localhost:5003
```

The root URL is the snapshot picker; every page lives under
`/s/<snapshot>/<section>`:

`Holdings · Status · Transactions · Trends · Budget · Projections · Import · Merchants · Categories`

Holdings is the finances landing page and combines cash accounts, credit cards,
loans, and assets in a dense spreadsheet-style editor (`?edit=1`). Status is
the key-numbers and funding-analysis dashboard. Import accepts drag-and-drop
statement files, stages them, and confirms/rejects per file.

## CLI

One Click tree over both domains. Global options: `--db` (default
`$FINTRACK_DB`) and `--snapshot` (defaults to the sole snapshot when there is
only one):

```bash
uv run python fintrack.py --help
uv run python fintrack.py status                                  # key-numbers rollup
uv run python fintrack.py snapshots list
uv run python fintrack.py accounts list
uv run python fintrack.py import statements/ --account "Chase Checking"
uv run python fintrack.py staging list                            # then: staging confirm <id>
uv run python fintrack.py balance set "Wallet" 42.50              # manual balance point
uv run python fintrack.py balance history "Chase Checking"
uv run python fintrack.py merchants list
uv run python fintrack.py report monthly
uv run python fintrack.py report trends
uv run python fintrack.py budget
uv run python fintrack.py assets
uv run python fintrack.py funding
uv run python fintrack.py project --months 12 [--estimate]
uv run python fintrack.py serve
```

## Database

SQLite via SQLAlchemy Core, with Alembic migrations:

```bash
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"
```

## Migrating from the legacy apps

`migrate-legacy` merges the two predecessor databases into one fintrack DB.
It only ever reads the legacy databases. Two-step flow:

```bash
# 1. Scan both DBs and write an account-mapping template
uv run python fintrack.py migrate-legacy \
  --spending-db ../spending/spending.db \
  --finances-db ../finances/finances.db \
  --mapping mapping.yaml --write-template

# 2. Review/edit mapping.yaml (account pairings, snapshot assignments), then:
uv run python fintrack.py migrate-legacy \
  --spending-db ../spending/spending.db \
  --finances-db ../finances/finances.db \
  --mapping mapping.yaml --dry-run     # preview; drop --dry-run to apply
```

## Docker

```bash
docker build -t fintrack .
docker run -p 5003:5003 -v fintrack-data:/app/data fintrack
```

The container runs `alembic upgrade head` on startup and stores the database
at `/app/data/fintrack.db` (override with `FINTRACK_DB`). CI publishes the
image on pushes to `main`.

Set `FINTRACK_PREVIEW_SEED=1` to seed three throwaway demo snapshots on boot,
so the app has something to show on first load and every render density has a
fixture: **Dense Household** (many accounts, cards, loans, assets, budget lines
and several months of transactions — deliberately overflows every sheet),
**Sparse Household** (a handful of records), and **Empty Household** (no data
at all):

```bash
docker run -p 5003:5003 -e FINTRACK_PREVIEW_SEED=1 fintrack
# or locally, without Docker:
FINTRACK_DB=preview.db uv run python scripts/seed_example.py && mise run serve
```

The seed is idempotent per snapshot — it skips any that already exist — and
only ever writes those three demo snapshots.

## PR preview environments (AWS Lambda)

`.github/workflows/pr-preview.yml` builds a Lambda container image, deploys it
as an **AWS Lambda function with a public Function URL** for every pull request,
comments the URL on the PR, and **deletes the function when the PR is merged or
closed**. Authentication uses GitHub OIDC, so there are **no AWS keys stored as
GitHub secrets**.

The workflow is **inert until configured**: both jobs are skipped while the
`AWS_DEPLOY_ROLE_ARN` repository variable is unset. Follow the
[AWS setup guide](docs/aws-preview-setup.md) to provision the IAM roles, ECR
repository, and cost controls, then set the repository variables to activate
previews.

> **Heads-up:** Lambda Function URLs are **public and unauthenticated** —
> anyone with the link can view the preview. The preview only ever contains the
> fake demo households seed data (never a real database), the database is
> ephemeral (rebuilt on each cold start), and the function is deleted on PR
> close. First load after idle may take a few seconds. Treat the URL as
> shareable-but-guessable, not private.

### Cost

Lambda previews scale to zero — you pay only for actual requests. A day of
review with occasional page loads costs fractions of a cent; ECR image storage
is a few cents. An idle repo with no open PRs costs nothing. The ECR lifecycle
policy auto-expires old images to keep storage near zero.
