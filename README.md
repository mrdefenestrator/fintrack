# fintrack

Unified personal finance tracker — the merge of two predecessor apps:

- **spending** — transaction ledger: bank/CC statement import (OFX/CSV), staging + dedup,
  Claude-based merchant classification, corrections overlay, spending trends.
- **finances** — net-worth and cash-flow tracker: accounts, scheduled budget entries,
  assets/debts, funding analysis, snapshot-scoped households.

One Flask app (port 5003, env `FINTRACK_DB`) serves both domains under
`/s/<snapshot>/<section>`: Status · Accounts · Transactions · Trends · Budget ·
Assets · Import · Merchants. The root URL is the snapshot picker.

The merge design lives in the spending repo at
`docs/superpowers/specs/2026-07-15-fintrack-merge-design.md` (to be moved here as
DESIGN.md during the docs phase).

## Setup

```bash
mise run setup      # uv sync
mise run test       # format check, lint, unit + e2e tests
mise run serve      # web app on http://localhost:5003
```

## CLI

```bash
uv run python fintrack.py migrate-legacy --help   # one-time legacy DB import
uv run python spending.py --help                  # ledger CLI (until consolidation)
uv run python finances.py --help                  # net-worth CLI (until consolidation)
```

## Migrating from the legacy apps

```bash
uv run python fintrack.py migrate-legacy \
  --spending-db ../spending/spending.db \
  --finances-db ../finances/finances.db \
  --mapping mapping.yaml --write-template
# review mapping.yaml, then rerun without --write-template (add --dry-run to preview)
```
