# fintrack

Unified personal finance tracker — the merge of two predecessor apps:

- **spending** — transaction ledger: bank/CC statement import (OFX/CSV), staging + dedup,
  Claude-based merchant classification, corrections overlay, spending trends.
- **finances** — net-worth and cash-flow tracker: accounts, scheduled budget entries,
  assets/debts, funding analysis, snapshot-scoped households.

The merge design lives in the spending repo at
`docs/superpowers/specs/2026-07-15-fintrack-merge-design.md` (to be moved here as DESIGN.md
during consolidation).

## Status

**Phase 0**: both apps live here verbatim and run independently.

| App | Package | Web | CLI | Env var |
|-----|---------|-----|-----|---------|
| spending | `spending/` + `web_spending/` | port 5002 | `spending.py` | `SPENDING_DB` |
| finances | `finances/` + `web_finances/` | port 5001 | `finances.py` | `FINANCES_DB` |

## Setup

```bash
mise run setup      # uv sync
mise run test       # format check, lint, unit + e2e tests (both apps)
mise run serve-spending
mise run serve-finances
```
