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

Set `FINTRACK_PREVIEW_SEED=1` to seed a throwaway **Example Household**
snapshot (a few accounts, budget entries, a home + mortgage) on boot, so the
app has something to show on first load:

```bash
docker run -p 5003:5003 -e FINTRACK_PREVIEW_SEED=1 fintrack
# or locally, without Docker:
FINTRACK_DB=preview.db uv run python scripts/seed_example.py && mise run serve
```

The seed is idempotent — it skips if the snapshot already exists — and only
ever writes that one snapshot.

## PR preview environments (AWS App Runner)

`.github/workflows/pr-preview.yml` builds the image, deploys a **seeded,
throwaway instance of the app to [AWS App Runner](https://aws.amazon.com/apprunner/)**
on a public HTTPS URL for every pull request, comments the URL on the PR, and
**deletes the instance when the PR is merged or closed**. Authentication uses
GitHub OIDC, so there are **no AWS keys stored as GitHub secrets**.

The workflow is **inert until configured**: both jobs are skipped while the
`AWS_DEPLOY_ROLE_ARN` repository variable is unset. To turn it on, do the
one-time AWS setup below, then set the repository variables.

> **Heads-up:** App Runner service URLs are **public and unauthenticated** —
> anyone with the link can view the preview. The preview only ever contains the
> fake **Example Household** seed data (never a real database), the filesystem
> is ephemeral, and the instance is deleted on PR close. Treat the URL as
> shareable-but-guessable, not private.

### One-time AWS setup

Replace `<ACCOUNT_ID>` with your AWS account ID and pick a
[Region that supports App Runner](https://docs.aws.amazon.com/general/latest/gr/apprunner.html)
(e.g. `us-east-1`). The ECR repository is auto-created by the workflow on first
run, so you don't need to make it yourself.

**1. Create the GitHub OIDC identity provider** (once per account):

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com
```

**2. Create the App Runner → ECR access role** (App Runner assumes this to pull
the image). Trust `build.apprunner.amazonaws.com`:

```bash
aws iam create-role --role-name fintrack-apprunner-ecr-access \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": { "Service": "build.apprunner.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }]
  }'
aws iam attach-role-policy --role-name fintrack-apprunner-ecr-access \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
```

**3. Create the deploy role** that GitHub Actions assumes via OIDC. Its trust
policy is scoped to this repository (adjust the `sub` for a different
owner/repo, or tighten it to `:pull_request` / a branch):

```bash
aws iam create-role --role-name fintrack-pr-preview-deployer \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": { "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com" },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
        "StringLike": { "token.actions.githubusercontent.com:sub": "repo:mrdefenestrator/fintrack:*" }
      }
    }]
  }'
```

Attach a scoped permissions policy (ECR push/cleanup, App Runner lifecycle, and
`PassRole` for the access role from step 2):

```bash
aws iam put-role-policy --role-name fintrack-pr-preview-deployer \
  --policy-name fintrack-pr-preview --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      { "Sid": "ECRAuth", "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*" },
      { "Sid": "ECR", "Effect": "Allow", "Resource": "*", "Action": [
          "ecr:CreateRepository", "ecr:DescribeRepositories",
          "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
          "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
          "ecr:ListImages", "ecr:BatchDeleteImage" ] },
      { "Sid": "AppRunner", "Effect": "Allow", "Resource": "*", "Action": [
          "apprunner:CreateService", "apprunner:UpdateService",
          "apprunner:DeleteService", "apprunner:DescribeService",
          "apprunner:ListServices" ] },
      { "Sid": "AppRunnerSLR", "Effect": "Allow", "Action": "iam:CreateServiceLinkedRole",
        "Resource": "*",
        "Condition": { "StringEquals": { "iam:AWSServiceName": "apprunner.amazonaws.com" } } },
      { "Sid": "PassAccessRole", "Effect": "Allow", "Action": "iam:PassRole",
        "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/fintrack-apprunner-ecr-access",
        "Condition": { "StringEquals": { "iam:PassedToService": "apprunner.amazonaws.com" } } }
    ]
  }'
```

**4. Set the repository variables** at **Settings → Secrets and variables →
Actions → Variables** (these are variables, not secrets — none are sensitive):

| Variable | Required | Example / default |
|----------|----------|-------------------|
| `AWS_REGION` | ✅ | `us-east-1` |
| `AWS_DEPLOY_ROLE_ARN` | ✅ | `arn:aws:iam::<ACCOUNT_ID>:role/fintrack-pr-preview-deployer` |
| `APPRUNNER_ACCESS_ROLE_ARN` | ✅ | `arn:aws:iam::<ACCOUNT_ID>:role/fintrack-apprunner-ecr-access` |
| `ECR_REPOSITORY` | — | defaults to `fintrack-preview` |
| `APPRUNNER_CPU` | — | defaults to `1024` (1 vCPU) |
| `APPRUNNER_MEMORY` | — | defaults to `2048` (2 GB) |

Open a PR and, once the deploy job finishes (App Runner takes a few minutes to
go green), the preview URL is posted as a PR comment.

### Cost

You pay only while a preview exists (per-PR create → delete on close). App
Runner bills a small provisioned-memory charge plus active compute; a 1 vCPU /
2 GB preview alive for a day of review is on the order of pennies to ~$1, and
ECR image storage is a few cents. Nothing is always-on — there is no load
balancer or NAT gateway in this setup — so an idle repo with no open PRs costs
essentially nothing.
