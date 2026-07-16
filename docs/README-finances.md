# Finances Tracker

A Python tool for tracking accounts, scheduled income and expenses, assets, and debts. Computes key numbers (liquid total, projected change to end of month, non-liquid net) and shows current status.

See [DESIGN.md](DESIGN.md) for goals, concepts, and business rules.

## Project Structure

```text
├── data/                   # Source YAML files (for import only)
│   └── example.yaml        # Example data file
├── web/                    # Flask web app
│   ├── app.py              # Flask application and DB init
│   └── templates/          # Jinja2 templates (base, status, accounts, budget, assets)
├── finances/               # Shared package
│   ├── __init__.py         # Re-exports for CLI and web
│   ├── types.py            # TypedDict definitions
│   ├── loader.py           # load_finances_from_db
│   ├── db.py               # Engine factory and init_db
│   ├── models.py           # SQLAlchemy Core table definitions
│   ├── yaml_import.py      # Import YAML files as named snapshots
│   ├── repository/         # CRUD operations (accounts, budget, assets, snapshots)
│   ├── calculations.py
│   ├── filters.py
│   ├── formatting.py
│   ├── tables.py
│   └── cli.py              # CLI commands and main()
├── migrations/             # Alembic schema migrations
├── tests/                  # pytest test suite
├── finances.py             # CLI entrypoint
├── alembic.ini             # Alembic configuration
├── pyproject.toml
└── mise.toml               # mise task runner
```

## Prerequisites

- [mise](https://mise.jdx.dev/) — task runner (also installs uv)

## Setup

```bash
mise install
mise run setup
```

## Data Migration

If you have existing YAML data files, import them into the SQLite database once:

```bash
# Import a single file (snapshot name defaults to the file stem)
uv run python -m finances.yaml_import data/finances.yaml

# Import all YAML files in a directory at once
uv run python -m finances.yaml_import data/

# Specify a custom DB path or snapshot name
uv run python -m finances.yaml_import data/finances.yaml --db /path/to/finances.db --name mike
```

The database defaults to `finances.db` in the project root. Set `FINANCES_DB` to point elsewhere.

## Development

```bash
# Run unit tests with coverage
mise run test-unit

# Run all CI checks (format, lint, unit tests, e2e)
mise run test

# Format / lint
mise run format
mise run lint
```

## Usage

### Web GUI

```bash
# Start the web server (uses finances.db by default)
mise run serve

# Use a custom database
FINANCES_DB=/path/to/finances.db mise run serve
```

Available at http://localhost:5001. Supports multiple snapshots (households) via the file picker in the header.

For mobile access on the same network:
```bash
ipconfig getifaddr en0      # macOS
hostname -I | awk '{print $1}'  # Linux
```

### CLI

All commands operate on a named snapshot in the database.

```bash
# View status summary
uv run python finances.py <snapshot> status

# List accounts (--show-id exposes IDs for edit/delete)
uv run python finances.py <snapshot> accounts
uv run python finances.py <snapshot> accounts --show-id

# Accounts CRUD
uv run python finances.py <snapshot> accounts add --name "Checking" --type checking --balance 1000
uv run python finances.py <snapshot> accounts edit <id> --balance 1250
uv run python finances.py <snapshot> accounts delete <id>

# Budget (combined income + expenses view)
uv run python finances.py <snapshot> budget
uv run python finances.py <snapshot> budget --annual

# Income / expenses
uv run python finances.py <snapshot> income --show-id
uv run python finances.py <snapshot> income add --description Salary --amount 5000 --recurrence monthly
uv run python finances.py <snapshot> expenses add --description Rent --amount 1500 --recurrence monthly

# Assets and debts
uv run python finances.py <snapshot> assets
uv run python finances.py <snapshot> debts

# Use a custom database path
uv run python finances.py --db /path/to/finances.db <snapshot> status
```

**Key numbers shown in `status`:**
- **(1)** Liquid total — sum of liquid account balances
- **(2)** Accounts total — liquid minus credit card debts
- **(3)** Projected change to end of month (continuous items prorated by days remaining)
- **(4)** Expected end-of-month total (2 + 3)
- **(5)** Non-liquid net (paired asset/debt)
- **(6)** Non-liquid net (total)
