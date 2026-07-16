# CLAUDE.md

## Project Overview

Personal spending tracker that ingests bank/CC statements, classifies transactions via Claude API, and provides monthly spending analysis. Has both a CLI and Flask web UI. See DESIGN.md for the full spec.

## Tech Stack

- Python 3.12 (managed via `uv`, see pyproject.toml)
- Package manager: `uv` (pyproject.toml, `uv sync`), installed via `mise`
- Virtual environment: `.venv/`
- Flask + HTMX for web UI (port 5002)
- SQLite database via SQLAlchemy Core
- Alembic for schema migrations
- Claude API (Haiku) for merchant classification
- Click for CLI

## Common Commands

```bash
# Setup
mise run setup             # Install all deps into .venv via uv sync

# Run tests
mise run test              # all CI checks (format, lint, unit tests)
mise run test-unit         # pytest unit tests with coverage

# Format / Lint
mise run format            # ruff formatter
mise run lint              # ruff linter

# CLI usage
uv run python spending.py accounts list
uv run python spending.py import ./statements/ --account "Chase Visa"
uv run python spending.py status

# Web UI
mise run serve

# Database migrations
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"
```

## Project Structure

- `spending/` — Shared package (models, repository, importer, classifier, CLI)
- `spending/repository/` — Database queries organized by domain
- `spending/importer/` — Statement parsers (OFX, CSV), normalization, dedup
- `web/` — Flask app with Jinja2/Tailwind/HTMX templates
- `tests/` — pytest test suite
- `configs/` — Category list, institution configs, normalization rules
- `migrations/` — Alembic migration scripts
- `spending.py` — CLI entrypoint

## Code Style

- **Ruff** for formatting (88-char line length) and linting (E501 ignored)
- Modern Python: type hints, f-strings, TypedDicts
- Repository pattern: all DB queries go through `spending/repository/`
- SQLAlchemy Core (not ORM) for query building

## Key Architecture Decisions

- SQLite for storage, SQLAlchemy Core for queries, Alembic for migrations
- Corrections overlay: raw imported data is immutable, corrections stored separately
- Merchant cache: normalized merchant name → category, populated by Claude API
- Only merchant names sent to Claude API (privacy)
- Staging area: imports are pending until reviewed and confirmed
- Sequence-aware fingerprinting for deduplication
