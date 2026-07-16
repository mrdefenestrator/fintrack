# CLAUDE.md

## Project Overview

Finances tracker for accounts, scheduled income/expenses, assets, and debts. Computes liquid totals, projected monthly change, and non-liquid net worth from a YAML data file. Has both a CLI (`finances.py`) and a Flask web UI (`web/app.py`). See [DESIGN.md](DESIGN.md) for goals, concepts, and business rules.

## Tech Stack

- Python 3.12 (managed via `uv`, see `requires-python` in pyproject.toml)
- Package manager: `uv` (pyproject.toml, `uv sync`), installed via `mise`
- Virtual environment: `.venv/`
- Flask for web UI (port 5001)
- YAML files as the data store (no database)

## Common Commands

```bash
# Setup
mise run setup             # Install all deps into .venv via uv sync

# Run tests
mise run test              # all CI checks (format, lint, validate, unit tests, e2e)
mise run test-unit         # pytest unit tests with coverage (excludes e2e)
mise run test-web          # Playwright web browser tests (requires browser)

# Format code
mise run format            # ruff formatter
mise run format-check      # check formatting without changing files

# Lint
mise run lint              # ruff linter
mise run lint-fix          # auto-fix lint issues

# Validate YAML files
mise run validate

# CLI usage
uv run python finances.py data/finances.yaml status
uv run python finances.py data/finances.yaml accounts
uv run python finances.py data/finances.yaml budget

# Web UI
mise run serve
```

## Project Structure

- `finances/` — Shared package (loader, calculations, filters, formatting, tables, writer, cli, types)
- `tests/` — pytest test suite (1:1 correspondence with source files)
- `web/` — Flask app with Jinja2/Tailwind/HTMX templates
- `data/` — Finances YAML data files
- `finances.py` — CLI entrypoint (runs finances.cli.main)
- `validate_yaml.py` — Schema validation script
- `schema.yaml` — JSON schema for finances YAML files

## Code Style

- **Ruff** for formatting (88-char line length) and linting (E501 ignored)
- Modern Python: type hints, f-strings, TypedDicts
- PEP 8 compliant

## Key Architecture Decisions

- Single YAML file per snapshot (one household)
- Writer validates against schema.yaml after every mutation
- Reference integrity enforced (can't delete accounts/assets with references)
- Budget proration: continuous items prorated by days remaining in month
- CLI and web share the same `finances` package (loader, writer, calculations)
- `yaml.safe_load` for parsing YAML data files
- Key numbers: (1) liquid total, (2) accounts total (liquid - CC), (3) projected change, (4) expected end-of-month, (5) paired asset-debt net, (6) total non-liquid net
