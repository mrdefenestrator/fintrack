# Spending Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a personal spending tracker that ingests bank/CC statements, classifies transactions via Claude API with local caching, and provides monthly spending analysis through a Flask/HTMX web UI and CLI.

**Architecture:** Repository pattern with SQLAlchemy Core over SQLite. Shared `spending` package consumed by both Flask web UI and Click CLI. Import pipeline: parse → normalize → dedup → classify → stage. Corrections overlay (immutable raw data, separate corrections layer).

**Tech Stack:** Python 3.12, Flask, HTMX, SQLAlchemy Core, Alembic, Click, Anthropic SDK, ofxparse, Tailwind CSS (CDN), uv, mise, ruff, pytest

**Spec:** `docs/superpowers/specs/2026-04-14-spending-tracker-design.md`

---

## File Structure

```
spending/
├── spending/                    # shared package
│   ├── __init__.py
│   ├── cli.py                   # click CLI entrypoint and command groups
│   ├── db.py                    # SQLAlchemy engine creation, init_db
│   ├── models.py                # SQLAlchemy Core table definitions (6 tables)
│   ├── repository/
│   │   ├── __init__.py          # re-exports all repository functions
│   │   ├── accounts.py          # accounts CRUD
│   │   ├── categories.py        # categories CRUD + seed from YAML
│   │   ├── transactions.py      # transaction queries with resolved category/merchant
│   │   ├── corrections.py       # transaction and merchant-level corrections
│   │   ├── merchants.py         # merchant_cache CRUD
│   │   ├── imports.py           # import record management, staging
│   │   └── aggregations.py      # monthly summaries, rolling averages, trends
│   ├── importer/
│   │   ├── __init__.py          # ImportResult type, run_import orchestrator
│   │   ├── ofx.py               # OFX/QFX parser
│   │   ├── csv_parser.py        # CSV parser with per-institution configs
│   │   ├── normalize.py         # merchant name normalization pipeline
│   │   └── dedup.py             # fingerprinting and deduplication
│   ├── classifier.py            # Claude API batch classification + cache
│   └── types.py                 # shared TypedDicts
├── web/
│   ├── __init__.py
│   ├── app.py                   # Flask app factory
│   ├── routes/
│   │   ├── __init__.py          # registers all blueprints
│   │   ├── monthly.py           # monthly category breakdown + drilldown
│   │   ├── transactions.py      # transaction list, filters, inline edit, bulk
│   │   ├── trends.py            # preset period summaries + sparklines
│   │   ├── merchants.py         # merchant management view
│   │   └── imports.py           # file upload, staging review, batch confirm
│   ├── static/
│   │   ├── htmx.min.js          # HTMX library
│   │   └── app.js               # drag-and-drop, bulk select, keyboard nav
│   └── templates/
│       ├── base.html            # layout with tab nav, Tailwind CDN, HTMX
│       ├── monthly.html         # monthly tab content
│       ├── transactions.html    # transactions tab content
│       ├── trends.html          # trends tab content
│       ├── merchants.html       # merchants tab content
│       ├── import.html          # import tab content
│       └── partials/
│           ├── monthly_table.html
│           ├── monthly_drilldown.html
│           ├── transaction_rows.html
│           ├── transaction_edit.html
│           ├── trends_table.html
│           ├── merchant_rows.html
│           ├── merchant_edit.html
│           ├── import_batch.html
│           └── sparkline.svg     # Jinja2 macro for inline SVG sparklines
├── tests/
│   ├── conftest.py              # shared fixtures (in-memory DB, seeded categories)
│   ├── test_models.py
│   ├── test_repository/
│   │   ├── test_accounts.py
│   │   ├── test_categories.py
│   │   ├── test_transactions.py
│   │   ├── test_corrections.py
│   │   ├── test_merchants.py
│   │   ├── test_imports.py
│   │   └── test_aggregations.py
│   ├── test_importer/
│   │   ├── conftest.py          # sample OFX/CSV fixture files
│   │   ├── test_ofx.py
│   │   ├── test_csv_parser.py
│   │   ├── test_normalize.py
│   │   └── test_dedup.py
│   ├── test_classifier.py
│   ├── test_cli.py
│   └── e2e/                     # Playwright tests (future)
├── configs/
│   ├── categories.yaml          # default category list
│   ├── institutions/            # per-institution CSV configs
│   │   └── example.yaml
│   └── normalization.yaml       # merchant name normalization rules
├── migrations/
│   ├── env.py
│   └── versions/
├── alembic.ini
├── spending.py                  # CLI entrypoint: from spending.cli import cli; cli()
├── pyproject.toml
├── mise.toml
├── .gitignore
├── Dockerfile
├── docker-compose.yml
└── CLAUDE.md
```

**Implementation note:** The spec lists `repository.py` as a single file, but we split it into a package (`repository/`) for focused, testable units. The spec lists `importer/csv.py` — we use `csv_parser.py` to avoid shadowing the stdlib `csv` module. We add a `normalized_merchant` column to `transactions` (not in the spec's table) so the resolved-merchant JOIN to `merchant_cache` can happen in SQL.

---

### Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `mise.toml`
- Create: `.gitignore`
- Create: `spending.py`
- Create: `spending/__init__.py`
- Create: `web/__init__.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "spending"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "Flask>=3.0",
    "SQLAlchemy>=2.0",
    "alembic>=1.13",
    "ofxparse>=0.21",
    "anthropic>=0.40",
    "click>=8.1",
    "PyYAML>=6.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-cov>=7.0",
    "ruff>=0.8",
    "playwright>=1.40",
    "pytest-playwright>=0.4",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "e2e: end-to-end browser tests (deselect with '-m not e2e')",
]

[tool.ruff]
line-length = 88

[tool.ruff.lint]
ignore = ["E501"]
```

- [ ] **Step 2: Create mise.toml**

```toml
[tools]
uv = "latest"

[tasks.setup]
run = "uv sync"
description = "Install all dependencies into .venv"

[tasks.format]
run = "uv run ruff format spending/ web/ tests/ spending.py"
description = "Format code with ruff"

[tasks.format-check]
run = "uv run ruff format --check spending/ web/ tests/ spending.py"
description = "Check code formatting"

[tasks.lint]
run = "uv run ruff check spending/ web/ tests/ spending.py"
description = "Lint with ruff"

[tasks.lint-fix]
run = "uv run ruff check --fix spending/ web/ tests/ spending.py"
description = "Fix lint issues with ruff"

[tasks.test]
depends = ["format-check", "lint", "test-unit"]
description = "Run all CI checks"

[tasks.test-unit]
run = "uv run pytest tests/ -v --cov=spending --cov-report=term-missing -m 'not e2e'"
description = "Run unit tests with coverage"

[tasks.serve]
run = "uv run python web/app.py"
description = "Start the web server"
```

- [ ] **Step 3: Create .gitignore**

```
.venv/
__pycache__/
*.pyc
.DS_Store
.coverage
.ruff_cache
.pytest_cache
*.db
data/
docker-compose.yml
```

- [ ] **Step 4: Create directory structure**

```bash
mkdir -p spending/repository spending/importer web/routes web/static web/templates/partials tests/test_repository tests/test_importer tests/e2e configs/institutions migrations/versions
```

Create empty `__init__.py` files:

`spending/__init__.py`:
```python
```

`spending/repository/__init__.py`:
```python
```

`spending/importer/__init__.py`:
```python
```

`web/__init__.py`:
```python
```

`web/routes/__init__.py`:
```python
```

`spending.py`:
```python
from spending.cli import cli

if __name__ == "__main__":
    cli()
```

- [ ] **Step 5: Run setup and verify**

Run: `mise run setup`
Expected: Dependencies install successfully, `.venv/` created

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml mise.toml .gitignore spending.py spending/ web/ tests/ configs/ migrations/
git commit -m "feat: project scaffold with dependencies and tooling"
```

---

### Task 2: Database & Models

**Files:**
- Create: `spending/models.py`
- Create: `spending/db.py`
- Create: `tests/conftest.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write test for table creation**

`tests/conftest.py`:
```python
import pytest
from sqlalchemy import create_engine
from spending.models import metadata


@pytest.fixture
def engine():
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    return engine


@pytest.fixture
def conn(engine):
    with engine.connect() as connection:
        yield connection
```

`tests/test_models.py`:
```python
from sqlalchemy import inspect


def test_all_tables_created(engine):
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    expected = {
        "accounts",
        "imports",
        "transactions",
        "merchant_cache",
        "transaction_corrections",
        "categories",
    }
    assert expected == table_names


def test_transactions_has_normalized_merchant(engine):
    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("transactions")}
    assert "normalized_merchant" in columns
    assert "raw_description" in columns
    assert "fingerprint" in columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'spending.models'`

- [ ] **Step 3: Write models.py**

`spending/models.py`:
```python
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
)

metadata = MetaData()


def _utcnow():
    return datetime.now(timezone.utc)


accounts = Table(
    "accounts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, unique=True, nullable=False),
    Column("institution", String, nullable=False),
    Column("account_type", String, nullable=False),
    Column("created_at", DateTime, default=_utcnow),
)

imports = Table(
    "imports",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    Column("filename", String, nullable=False),
    Column("file_hash", String, nullable=False),
    Column("imported_at", DateTime, default=_utcnow),
    Column("status", String, nullable=False, default="staging"),
)

transactions = Table(
    "transactions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("import_id", Integer, ForeignKey("imports.id"), nullable=False),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    Column("date", Date, nullable=False),
    Column("amount", Numeric(10, 2), nullable=False),
    Column("raw_description", String, nullable=False),
    Column("normalized_merchant", String, nullable=False),
    Column("fingerprint", String, nullable=False),
    Column("created_at", DateTime, default=_utcnow),
)

merchant_cache = Table(
    "merchant_cache",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("merchant_name", String, unique=True, nullable=False),
    Column("category", String, nullable=False),
    Column("source", String, nullable=False),
    Column("created_at", DateTime, default=_utcnow),
    Column("updated_at", DateTime, default=_utcnow),
)

transaction_corrections = Table(
    "transaction_corrections",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "transaction_id",
        Integer,
        ForeignKey("transactions.id"),
        unique=True,
        nullable=False,
    ),
    Column("category", String, nullable=True),
    Column("merchant_name", String, nullable=True),
    Column("notes", String, nullable=True),
    Column("created_at", DateTime, default=_utcnow),
)

categories = Table(
    "categories",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, unique=True, nullable=False),
    Column("sort_order", Integer, nullable=False),
)
```

- [ ] **Step 4: Write db.py**

`spending/db.py`:
```python
from pathlib import Path

from sqlalchemy import Engine, create_engine

from spending.models import metadata


def get_engine(db_path: str | Path = "spending.db") -> Engine:
    return create_engine(f"sqlite:///{db_path}")


def init_db(engine: Engine) -> None:
    metadata.create_all(engine)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: 2 tests PASS

- [ ] **Step 6: Commit**

```bash
git add spending/models.py spending/db.py tests/conftest.py tests/test_models.py
git commit -m "feat: SQLAlchemy Core table definitions and DB setup"
```

---

### Task 3: Alembic Setup

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/001_initial_schema.py`

- [ ] **Step 1: Initialize Alembic**

Run: `uv run alembic init migrations`
Expected: Creates `alembic.ini` and `migrations/` directory with `env.py`, `script.py.mako`, `versions/`

- [ ] **Step 2: Configure alembic.ini**

Edit `alembic.ini` — set the SQLite URL:

```ini
sqlalchemy.url = sqlite:///spending.db
```

- [ ] **Step 3: Configure migrations/env.py**

Replace the `target_metadata` line in `migrations/env.py`:

```python
from spending.models import metadata
target_metadata = metadata
```

- [ ] **Step 4: Generate initial migration**

Run: `uv run alembic revision --autogenerate -m "initial schema"`
Expected: Creates a migration file in `migrations/versions/`

- [ ] **Step 5: Verify migration runs**

Run: `uv run alembic upgrade head`
Expected: Creates `spending.db` with all 6 tables

Run: `uv run alembic downgrade base`
Expected: Drops all tables

Clean up: `rm spending.db`

- [ ] **Step 6: Commit**

```bash
git add alembic.ini migrations/
git commit -m "feat: Alembic migration setup with initial schema"
```

---

### Task 4: Categories Config & Repository

**Files:**
- Create: `configs/categories.yaml`
- Create: `spending/repository/categories.py`
- Create: `tests/test_repository/test_categories.py`
- Modify: `spending/repository/__init__.py`

- [ ] **Step 1: Create categories config**

`configs/categories.yaml`:
```yaml
categories:
  - name: Groceries
    sort_order: 1
  - name: Dining
    sort_order: 2
  - name: Transport
    sort_order: 3
  - name: Housing
    sort_order: 4
  - name: Utilities
    sort_order: 5
  - name: Subscriptions
    sort_order: 6
  - name: Healthcare
    sort_order: 7
  - name: Entertainment
    sort_order: 8
  - name: Shopping
    sort_order: 9
  - name: Travel
    sort_order: 10
  - name: Income
    sort_order: 11
  - name: Transfer
    sort_order: 12
  - name: Fees
    sort_order: 13
  - name: Other
    sort_order: 14
```

- [ ] **Step 2: Write tests**

`tests/test_repository/test_categories.py`:
```python
from spending.repository.categories import (
    seed_categories,
    list_categories,
    add_category,
    get_category_names,
    delete_category,
)


def test_seed_categories(conn):
    seed_categories(conn, "configs/categories.yaml")
    cats = list_categories(conn)
    assert len(cats) == 14
    assert cats[0]["name"] == "Groceries"
    assert cats[-1]["name"] == "Other"


def test_seed_categories_is_idempotent(conn):
    seed_categories(conn, "configs/categories.yaml")
    seed_categories(conn, "configs/categories.yaml")
    cats = list_categories(conn)
    assert len(cats) == 14


def test_add_category(conn):
    add_category(conn, name="Pets", sort_order=15)
    cats = list_categories(conn)
    names = [c["name"] for c in cats]
    assert "Pets" in names


def test_get_category_names(conn):
    seed_categories(conn, "configs/categories.yaml")
    names = get_category_names(conn)
    assert "Groceries" in names
    assert "Dining" in names
    assert len(names) == 14


def test_delete_category(conn):
    seed_categories(conn, "configs/categories.yaml")
    delete_category(conn, name="Other")
    names = get_category_names(conn)
    assert "Other" not in names
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_repository/test_categories.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement categories repository**

`spending/repository/categories.py`:
```python
from pathlib import Path

import yaml
from sqlalchemy import Connection, delete, insert, select

from spending.models import categories


def seed_categories(conn: Connection, config_path: str | Path) -> None:
    with open(config_path) as f:
        config = yaml.safe_load(f)

    for cat in config["categories"]:
        existing = conn.execute(
            select(categories).where(categories.c.name == cat["name"])
        ).fetchone()
        if not existing:
            conn.execute(
                insert(categories).values(
                    name=cat["name"], sort_order=cat["sort_order"]
                )
            )
    conn.commit()


def list_categories(conn: Connection) -> list[dict]:
    rows = conn.execute(
        select(categories).order_by(categories.c.sort_order)
    ).fetchall()
    return [dict(row._mapping) for row in rows]


def get_category_names(conn: Connection) -> list[str]:
    rows = conn.execute(
        select(categories.c.name).order_by(categories.c.sort_order)
    ).fetchall()
    return [row[0] for row in rows]


def add_category(conn: Connection, *, name: str, sort_order: int) -> None:
    conn.execute(insert(categories).values(name=name, sort_order=sort_order))
    conn.commit()


def edit_category(
    conn: Connection,
    category_id: int,
    *,
    name: str | None = None,
    sort_order: int | None = None,
) -> None:
    values = {}
    if name is not None:
        values["name"] = name
    if sort_order is not None:
        values["sort_order"] = sort_order
    if values:
        conn.execute(
            categories.update().where(categories.c.id == category_id).values(**values)
        )
        conn.commit()


def delete_category(conn: Connection, *, name: str) -> None:
    conn.execute(delete(categories).where(categories.c.name == name))
    conn.commit()
```

- [ ] **Step 5: Update repository __init__.py**

`spending/repository/__init__.py`:
```python
from spending.repository.categories import (
    add_category,
    delete_category,
    edit_category,
    get_category_names,
    list_categories,
    seed_categories,
)

__all__ = [
    "add_category",
    "delete_category",
    "edit_category",
    "get_category_names",
    "list_categories",
    "seed_categories",
]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_repository/test_categories.py -v`
Expected: 5 tests PASS

- [ ] **Step 7: Commit**

```bash
git add configs/categories.yaml spending/repository/ tests/test_repository/test_categories.py
git commit -m "feat: categories config, seed logic, and CRUD repository"
```

---

### Task 5: Accounts Repository & CLI

**Files:**
- Create: `spending/repository/accounts.py`
- Create: `tests/test_repository/test_accounts.py`
- Create: `spending/cli.py`
- Create: `tests/test_cli.py`
- Modify: `spending/repository/__init__.py`

- [ ] **Step 1: Write accounts repository tests**

`tests/test_repository/test_accounts.py`:
```python
import pytest
from sqlalchemy.exc import IntegrityError

from spending.repository.accounts import (
    add_account,
    delete_account,
    edit_account,
    get_account_by_name,
    list_accounts,
)


def test_add_and_list_accounts(conn):
    add_account(conn, name="Chase Visa", institution="Chase", account_type="credit_card")
    add_account(conn, name="BofA Checking", institution="BofA", account_type="checking")
    accts = list_accounts(conn)
    assert len(accts) == 2
    assert accts[0]["name"] == "Chase Visa"


def test_add_duplicate_name_fails(conn):
    add_account(conn, name="Chase Visa", institution="Chase", account_type="credit_card")
    with pytest.raises(IntegrityError):
        add_account(conn, name="Chase Visa", institution="Chase", account_type="credit_card")


def test_get_account_by_name(conn):
    add_account(conn, name="Chase Visa", institution="Chase", account_type="credit_card")
    acct = get_account_by_name(conn, "Chase Visa")
    assert acct is not None
    assert acct["institution"] == "Chase"


def test_get_account_by_name_not_found(conn):
    acct = get_account_by_name(conn, "Nonexistent")
    assert acct is None


def test_edit_account(conn):
    add_account(conn, name="Chase Visa", institution="Chase", account_type="credit_card")
    acct = get_account_by_name(conn, "Chase Visa")
    edit_account(conn, acct["id"], name="Chase Freedom")
    updated = get_account_by_name(conn, "Chase Freedom")
    assert updated is not None


def test_delete_account(conn):
    add_account(conn, name="Chase Visa", institution="Chase", account_type="credit_card")
    acct = get_account_by_name(conn, "Chase Visa")
    delete_account(conn, acct["id"])
    assert list_accounts(conn) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_repository/test_accounts.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement accounts repository**

`spending/repository/accounts.py`:
```python
from sqlalchemy import Connection, delete, insert, select

from spending.models import accounts


def add_account(
    conn: Connection, *, name: str, institution: str, account_type: str
) -> int:
    result = conn.execute(
        insert(accounts).values(
            name=name, institution=institution, account_type=account_type
        )
    )
    conn.commit()
    return result.inserted_primary_key[0]


def list_accounts(conn: Connection) -> list[dict]:
    rows = conn.execute(select(accounts).order_by(accounts.c.name)).fetchall()
    return [dict(row._mapping) for row in rows]


def get_account_by_name(conn: Connection, name: str) -> dict | None:
    row = conn.execute(
        select(accounts).where(accounts.c.name == name)
    ).fetchone()
    return dict(row._mapping) if row else None


def get_account_by_id(conn: Connection, account_id: int) -> dict | None:
    row = conn.execute(
        select(accounts).where(accounts.c.id == account_id)
    ).fetchone()
    return dict(row._mapping) if row else None


def edit_account(
    conn: Connection,
    account_id: int,
    *,
    name: str | None = None,
    institution: str | None = None,
    account_type: str | None = None,
) -> None:
    values = {}
    if name is not None:
        values["name"] = name
    if institution is not None:
        values["institution"] = institution
    if account_type is not None:
        values["account_type"] = account_type
    if values:
        conn.execute(
            accounts.update().where(accounts.c.id == account_id).values(**values)
        )
        conn.commit()


def delete_account(conn: Connection, account_id: int) -> None:
    conn.execute(delete(accounts).where(accounts.c.id == account_id))
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_repository/test_accounts.py -v`
Expected: 6 tests PASS

- [ ] **Step 5: Write CLI tests**

`tests/test_cli.py`:
```python
from click.testing import CliRunner

from spending.cli import cli


def test_accounts_list_empty(tmp_path):
    db = tmp_path / "test.db"
    runner = CliRunner()
    result = runner.invoke(cli, ["--db", str(db), "accounts", "list"])
    assert result.exit_code == 0
    assert "No accounts" in result.output


def test_accounts_add_and_list(tmp_path):
    db = tmp_path / "test.db"
    runner = CliRunner()
    runner.invoke(
        cli,
        [
            "--db", str(db),
            "accounts", "add",
            "--name", "Chase Visa",
            "--institution", "Chase",
            "--type", "credit_card",
        ],
    )
    result = runner.invoke(cli, ["--db", str(db), "accounts", "list"])
    assert result.exit_code == 0
    assert "Chase Visa" in result.output


def test_categories_list_seeded(tmp_path):
    db = tmp_path / "test.db"
    runner = CliRunner()
    result = runner.invoke(cli, ["--db", str(db), "categories", "list"])
    assert result.exit_code == 0
    assert "Groceries" in result.output
```

- [ ] **Step 6: Run CLI tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 7: Implement CLI**

`spending/cli.py`:
```python
import click
from sqlalchemy import create_engine

from spending.db import init_db
from spending.models import metadata
from spending.repository.accounts import (
    add_account,
    delete_account,
    edit_account,
    get_account_by_id,
    list_accounts,
)
from spending.repository.categories import (
    add_category,
    delete_category,
    edit_category,
    list_categories,
    seed_categories,
)


@click.group()
@click.option("--db", default="spending.db", help="Path to SQLite database")
@click.pass_context
def cli(ctx, db):
    """Spending tracker CLI."""
    ctx.ensure_object(dict)
    engine = create_engine(f"sqlite:///{db}")
    metadata.create_all(engine)
    ctx.obj["engine"] = engine
    with engine.connect() as conn:
        seed_categories(conn, "configs/categories.yaml")


@cli.group()
def accounts():
    """Manage accounts."""


@accounts.command("list")
@click.pass_context
def accounts_list(ctx):
    """List all accounts."""
    with ctx.obj["engine"].connect() as conn:
        accts = list_accounts(conn)
    if not accts:
        click.echo("No accounts found.")
        return
    for a in accts:
        click.echo(f"  [{a['id']}] {a['name']} ({a['institution']}, {a['account_type']})")


@accounts.command("add")
@click.option("--name", required=True)
@click.option("--institution", required=True)
@click.option("--type", "account_type", required=True, type=click.Choice(["checking", "savings", "credit_card"]))
@click.pass_context
def accounts_add(ctx, name, institution, account_type):
    """Add a new account."""
    with ctx.obj["engine"].connect() as conn:
        add_account(conn, name=name, institution=institution, account_type=account_type)
    click.echo(f"Added account: {name}")


@accounts.command("edit")
@click.argument("account_id", type=int)
@click.option("--name")
@click.option("--institution")
@click.option("--type", "account_type")
@click.pass_context
def accounts_edit(ctx, account_id, name, institution, account_type):
    """Edit an account."""
    with ctx.obj["engine"].connect() as conn:
        edit_account(conn, account_id, name=name, institution=institution, account_type=account_type)
    click.echo(f"Updated account {account_id}")


@accounts.command("delete")
@click.argument("account_id", type=int)
@click.pass_context
def accounts_delete(ctx, account_id):
    """Delete an account."""
    with ctx.obj["engine"].connect() as conn:
        acct = get_account_by_id(conn, account_id)
        if not acct:
            click.echo(f"Account {account_id} not found.")
            return
        delete_account(conn, account_id)
    click.echo(f"Deleted account {account_id}")


@cli.group()
def categories():
    """Manage categories."""


@categories.command("list")
@click.pass_context
def categories_list(ctx):
    """List all categories."""
    with ctx.obj["engine"].connect() as conn:
        cats = list_categories(conn)
    for c in cats:
        click.echo(f"  [{c['id']}] {c['name']} (order: {c['sort_order']})")


@categories.command("add")
@click.option("--name", required=True)
@click.option("--sort-order", required=True, type=int)
@click.pass_context
def categories_add(ctx, name, sort_order):
    """Add a new category."""
    with ctx.obj["engine"].connect() as conn:
        add_category(conn, name=name, sort_order=sort_order)
    click.echo(f"Added category: {name}")


@categories.command("edit")
@click.argument("category_id", type=int)
@click.option("--name")
@click.option("--sort-order", type=int)
@click.pass_context
def categories_edit(ctx, category_id, name, sort_order):
    """Edit a category."""
    with ctx.obj["engine"].connect() as conn:
        edit_category(conn, category_id, name=name, sort_order=sort_order)
    click.echo(f"Updated category {category_id}")


@categories.command("delete")
@click.argument("name")
@click.pass_context
def categories_delete(ctx, name):
    """Delete a category."""
    with ctx.obj["engine"].connect() as conn:
        delete_category(conn, name=name)
    click.echo(f"Deleted category: {name}")
```

- [ ] **Step 8: Update repository __init__.py**

Add the accounts imports to `spending/repository/__init__.py`:

```python
from spending.repository.accounts import (
    add_account,
    delete_account,
    edit_account,
    get_account_by_id,
    get_account_by_name,
    list_accounts,
)
from spending.repository.categories import (
    add_category,
    delete_category,
    edit_category,
    get_category_names,
    list_categories,
    seed_categories,
)

__all__ = [
    "add_account",
    "delete_account",
    "edit_account",
    "get_account_by_id",
    "get_account_by_name",
    "list_accounts",
    "add_category",
    "delete_category",
    "edit_category",
    "get_category_names",
    "list_categories",
    "seed_categories",
]
```

- [ ] **Step 9: Run all tests**

Run: `uv run pytest tests/test_repository/test_accounts.py tests/test_cli.py -v`
Expected: 9 tests PASS

- [ ] **Step 10: Commit**

```bash
git add spending/repository/ spending/cli.py tests/test_repository/test_accounts.py tests/test_cli.py
git commit -m "feat: accounts repository and CLI with accounts/categories commands"
```

---

### Task 6: Merchant Name Normalization

**Files:**
- Create: `configs/normalization.yaml`
- Create: `spending/importer/normalize.py`
- Create: `tests/test_importer/test_normalize.py`

- [ ] **Step 1: Create normalization config**

`configs/normalization.yaml`:
```yaml
prefixes:
  - "SQ *"
  - "SQ*"
  - "TST *"
  - "TST*"
  - "PAYPAL *"
  - "PAYPAL*"
  - "CKE *"
  - "SP *"
  - "SP*"
  - "APL*"
  - "GOOGLE *"

trailing_patterns:
  # Reference numbers: 3+ digits optionally preceded by # or *
  - "\\s*[#*]?\\d{3,}$"
  # Alphanumeric transaction IDs: 5+ chars with mixed letters/numbers/asterisks
  - "\\s+[A-Z0-9*]{5,}$"
  # City, State ZIP patterns: "CHICAGO IL", "NEW YORK NY 10001"
  - "\\s+[A-Z][A-Za-z ]+,?\\s+[A-Z]{2}\\s*\\d{0,5}$"
```

- [ ] **Step 2: Write tests**

`tests/test_importer/test_normalize.py`:
```python
from spending.importer.normalize import normalize_merchant


def test_strip_sq_prefix():
    assert normalize_merchant("SQ *COFFEE SHOP") == "COFFEE SHOP"


def test_strip_paypal_prefix():
    assert normalize_merchant("PAYPAL *NETFLIX") == "NETFLIX"


def test_strip_trailing_reference_number():
    assert normalize_merchant("COFFEE SHOP 8442") == "COFFEE SHOP"


def test_strip_trailing_transaction_id():
    assert normalize_merchant("AMZN MKTP US*2K7X9") == "AMZN MKTP"


def test_strip_city_state():
    assert normalize_merchant("COFFEE SHOP CHICAGO IL") == "COFFEE SHOP"


def test_strip_city_state_zip():
    assert normalize_merchant("TARGET STORE NEW YORK NY 10001") == "TARGET STORE"


def test_combined_prefix_and_trailing():
    assert normalize_merchant("SQ *COFFEE SHOP 8442 CHICAGO IL") == "COFFEE SHOP"


def test_collapse_whitespace():
    assert normalize_merchant("SOME   STORE   NAME") == "SOME STORE NAME"


def test_uppercase():
    assert normalize_merchant("whole foods market") == "WHOLE FOODS MARKET"


def test_already_clean():
    assert normalize_merchant("NETFLIX") == "NETFLIX"


def test_custom_config(tmp_path):
    config = tmp_path / "norm.yaml"
    config.write_text("prefixes:\n  - 'XX*'\ntrailing_patterns: []\n")
    assert normalize_merchant("XX*MYSTORE", config_path=str(config)) == "MYSTORE"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_importer/test_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement normalization**

`spending/importer/normalize.py`:
```python
import re
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_CONFIG = "configs/normalization.yaml"


@lru_cache(maxsize=1)
def _load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def normalize_merchant(
    raw_description: str, config_path: str = DEFAULT_CONFIG
) -> str:
    config = _load_config(config_path)
    text = raw_description.upper().strip()

    # Strip known prefixes
    for prefix in config.get("prefixes", []):
        if text.startswith(prefix.upper()):
            text = text[len(prefix) :]
            break

    # Strip trailing patterns
    for pattern in config.get("trailing_patterns", []):
        text = re.sub(pattern, "", text)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_importer/test_normalize.py -v`
Expected: 11 tests PASS

- [ ] **Step 6: Commit**

```bash
git add configs/normalization.yaml spending/importer/normalize.py tests/test_importer/test_normalize.py
git commit -m "feat: merchant name normalization pipeline with configurable rules"
```

---

### Task 7: OFX Parser

**Files:**
- Create: `spending/importer/ofx.py`
- Create: `spending/types.py`
- Create: `tests/test_importer/conftest.py`
- Create: `tests/test_importer/test_ofx.py`

- [ ] **Step 1: Define shared types**

`spending/types.py`:
```python
from datetime import date
from decimal import Decimal
from typing import TypedDict


class ParsedTransaction(TypedDict):
    date: date
    amount: Decimal
    raw_description: str


class ImportResult(TypedDict):
    transactions: list[ParsedTransaction]
    account_name: str | None
```

- [ ] **Step 2: Create test fixture OFX file**

`tests/test_importer/conftest.py`:
```python
import pytest


@pytest.fixture
def sample_ofx(tmp_path):
    content = """<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <STMTRS>
        <BANKACCTFROM>
          <ACCTID>1234567890</ACCTID>
        </BANKACCTFROM>
        <BANKTRANLIST>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20240115120000</DTPOSTED>
            <TRNAMT>-42.50</TRNAMT>
            <NAME>WHOLE FOODS MARKET #10234</NAME>
          </STMTTRN>
          <STMTTRN>
            <TRNTYPE>CREDIT</TRNTYPE>
            <DTPOSTED>20240116120000</DTPOSTED>
            <TRNAMT>1500.00</TRNAMT>
            <NAME>DIRECT DEPOSIT ACME CORP</NAME>
          </STMTTRN>
        </BANKTRANLIST>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>"""
    path = tmp_path / "test.ofx"
    path.write_text(content)
    return path


@pytest.fixture
def sample_csv(tmp_path):
    content = """Transaction Date,Post Date,Description,Category,Type,Amount
01/15/2024,01/16/2024,WHOLE FOODS MARKET #10234,Groceries,Sale,-42.50
01/16/2024,01/17/2024,DIRECT DEPOSIT ACME CORP,Income,Payment,1500.00
01/16/2024,01/17/2024,COFFEE SHOP,Food & Drink,Sale,-5.00
01/16/2024,01/17/2024,COFFEE SHOP,Food & Drink,Sale,-5.00
"""
    path = tmp_path / "test.csv"
    path.write_text(content)
    return path
```

- [ ] **Step 3: Write OFX parser tests**

`tests/test_importer/test_ofx.py`:
```python
from datetime import date
from decimal import Decimal

from spending.importer.ofx import parse_ofx


def test_parse_ofx_returns_transactions(sample_ofx):
    result = parse_ofx(sample_ofx)
    assert len(result["transactions"]) == 2


def test_parse_ofx_debit_transaction(sample_ofx):
    result = parse_ofx(sample_ofx)
    txn = result["transactions"][0]
    assert txn["date"] == date(2024, 1, 15)
    assert txn["amount"] == Decimal("-42.50")
    assert txn["raw_description"] == "WHOLE FOODS MARKET #10234"


def test_parse_ofx_credit_transaction(sample_ofx):
    result = parse_ofx(sample_ofx)
    txn = result["transactions"][1]
    assert txn["date"] == date(2024, 1, 16)
    assert txn["amount"] == Decimal("1500.00")


def test_parse_ofx_account_name_is_none(sample_ofx):
    result = parse_ofx(sample_ofx)
    assert result["account_name"] is None
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_importer/test_ofx.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 5: Implement OFX parser**

`spending/importer/ofx.py`:
```python
from datetime import date
from decimal import Decimal
from pathlib import Path

from ofxparse import OfxParser

from spending.types import ImportResult, ParsedTransaction


def parse_ofx(file_path: str | Path) -> ImportResult:
    with open(file_path, "rb") as f:
        ofx = OfxParser.parse(f)

    transactions: list[ParsedTransaction] = []

    account = ofx.account
    if account and account.statement:
        for txn in account.statement.transactions:
            transactions.append(
                ParsedTransaction(
                    date=txn.date.date() if hasattr(txn.date, "date") else txn.date,
                    amount=Decimal(str(txn.amount)),
                    raw_description=txn.payee or txn.memo or "",
                )
            )

    return ImportResult(transactions=transactions, account_name=None)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_importer/test_ofx.py -v`
Expected: 4 tests PASS

- [ ] **Step 7: Commit**

```bash
git add spending/types.py spending/importer/ofx.py tests/test_importer/conftest.py tests/test_importer/test_ofx.py
git commit -m "feat: OFX/QFX parser with shared transaction types"
```

---

### Task 8: CSV Parser with Institution Configs

**Files:**
- Create: `configs/institutions/example.yaml`
- Create: `spending/importer/csv_parser.py`
- Create: `tests/test_importer/test_csv_parser.py`

- [ ] **Step 1: Create example institution config**

`configs/institutions/example.yaml`:
```yaml
name: Example Bank Credit Card
institution: Example Bank
date_column: "Transaction Date"
amount_column: "Amount"
description_column: "Description"
date_format: "%m/%d/%Y"
account_name: null
header_pattern:
  - "Transaction Date"
  - "Post Date"
  - "Description"
  - "Category"
  - "Type"
  - "Amount"
```

- [ ] **Step 2: Write CSV parser tests**

`tests/test_importer/test_csv_parser.py`:
```python
from datetime import date
from decimal import Decimal

from spending.importer.csv_parser import parse_csv, detect_institution_config


def test_parse_csv(sample_csv, tmp_path):
    config_path = tmp_path / "inst.yaml"
    config_path.write_text(
        """
name: Test Bank
institution: Test
date_column: "Transaction Date"
amount_column: "Amount"
description_column: "Description"
date_format: "%m/%d/%Y"
account_name: "Test Card"
header_pattern:
  - "Transaction Date"
  - "Post Date"
  - "Description"
  - "Category"
  - "Type"
  - "Amount"
"""
    )
    result = parse_csv(sample_csv, str(config_path))
    assert len(result["transactions"]) == 4
    assert result["account_name"] == "Test Card"


def test_parse_csv_amounts(sample_csv, tmp_path):
    config_path = tmp_path / "inst.yaml"
    config_path.write_text(
        """
name: Test Bank
institution: Test
date_column: "Transaction Date"
amount_column: "Amount"
description_column: "Description"
date_format: "%m/%d/%Y"
account_name: null
header_pattern: []
"""
    )
    result = parse_csv(sample_csv, str(config_path))
    txn = result["transactions"][0]
    assert txn["date"] == date(2024, 1, 15)
    assert txn["amount"] == Decimal("-42.50")
    assert txn["raw_description"] == "WHOLE FOODS MARKET #10234"


def test_detect_institution_config(sample_csv, tmp_path):
    config_dir = tmp_path / "institutions"
    config_dir.mkdir()
    config_file = config_dir / "test.yaml"
    config_file.write_text(
        """
name: Test Bank
institution: Test
date_column: "Transaction Date"
amount_column: "Amount"
description_column: "Description"
date_format: "%m/%d/%Y"
account_name: null
header_pattern:
  - "Transaction Date"
  - "Post Date"
  - "Description"
  - "Category"
  - "Type"
  - "Amount"
"""
    )
    detected = detect_institution_config(sample_csv, str(config_dir))
    assert detected is not None
    assert "Test Bank" in open(detected).read()


def test_detect_institution_config_no_match(sample_csv, tmp_path):
    config_dir = tmp_path / "institutions"
    config_dir.mkdir()
    config_file = config_dir / "other.yaml"
    config_file.write_text(
        """
name: Other Bank
institution: Other
date_column: "Date"
amount_column: "Amt"
description_column: "Desc"
date_format: "%Y-%m-%d"
account_name: null
header_pattern:
  - "Date"
  - "Amt"
  - "Desc"
"""
    )
    detected = detect_institution_config(sample_csv, str(config_dir))
    assert detected is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_importer/test_csv_parser.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement CSV parser**

`spending/importer/csv_parser.py`:
```python
import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import yaml

from spending.types import ImportResult, ParsedTransaction


def parse_csv(file_path: str | Path, config_path: str | Path) -> ImportResult:
    with open(config_path) as f:
        config = yaml.safe_load(f)

    date_col = config["date_column"]
    amount_col = config["amount_column"]
    desc_col = config["description_column"]
    date_fmt = config["date_format"]

    transactions: list[ParsedTransaction] = []

    with open(file_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            txn_date = datetime.strptime(row[date_col].strip(), date_fmt).date()
            amount = Decimal(row[amount_col].strip().replace(",", ""))
            description = row[desc_col].strip()

            transactions.append(
                ParsedTransaction(
                    date=txn_date,
                    amount=amount,
                    raw_description=description,
                )
            )

    return ImportResult(
        transactions=transactions,
        account_name=config.get("account_name"),
    )


def detect_institution_config(
    csv_path: str | Path, configs_dir: str | Path
) -> str | None:
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            return None

    headers = [h.strip() for h in headers]

    configs_dir = Path(configs_dir)
    for config_file in configs_dir.glob("*.yaml"):
        with open(config_file) as f:
            config = yaml.safe_load(f)

        pattern = config.get("header_pattern", [])
        if pattern and all(col in headers for col in pattern):
            return str(config_file)

    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_importer/test_csv_parser.py -v`
Expected: 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add configs/institutions/example.yaml spending/importer/csv_parser.py tests/test_importer/test_csv_parser.py
git commit -m "feat: CSV parser with per-institution config and auto-detection"
```

---

### Task 9: Fingerprinting & Deduplication

**Files:**
- Create: `spending/importer/dedup.py`
- Create: `tests/test_importer/test_dedup.py`

- [ ] **Step 1: Write tests**

`tests/test_importer/test_dedup.py`:
```python
from datetime import date
from decimal import Decimal

from spending.importer.dedup import compute_fingerprints, deduplicate
from spending.types import ParsedTransaction


def test_compute_fingerprints_unique():
    txns = [
        ParsedTransaction(date=date(2024, 1, 15), amount=Decimal("-42.50"), raw_description="WHOLE FOODS"),
        ParsedTransaction(date=date(2024, 1, 16), amount=Decimal("-5.00"), raw_description="COFFEE SHOP"),
    ]
    fingerprints = compute_fingerprints(txns, account_id=1)
    assert len(fingerprints) == 2
    assert fingerprints[0] != fingerprints[1]


def test_compute_fingerprints_duplicate_same_day():
    """Two identical transactions get different fingerprints via sequence number."""
    txns = [
        ParsedTransaction(date=date(2024, 1, 16), amount=Decimal("-5.00"), raw_description="COFFEE SHOP"),
        ParsedTransaction(date=date(2024, 1, 16), amount=Decimal("-5.00"), raw_description="COFFEE SHOP"),
    ]
    fingerprints = compute_fingerprints(txns, account_id=1)
    assert fingerprints[0] != fingerprints[1]


def test_compute_fingerprints_deterministic():
    txns = [
        ParsedTransaction(date=date(2024, 1, 15), amount=Decimal("-42.50"), raw_description="WHOLE FOODS"),
    ]
    fp1 = compute_fingerprints(txns, account_id=1)
    fp2 = compute_fingerprints(txns, account_id=1)
    assert fp1 == fp2


def test_deduplicate_removes_exact_matches():
    txns = [
        ParsedTransaction(date=date(2024, 1, 15), amount=Decimal("-42.50"), raw_description="WHOLE FOODS"),
        ParsedTransaction(date=date(2024, 1, 16), amount=Decimal("-5.00"), raw_description="COFFEE SHOP"),
    ]
    fingerprints = compute_fingerprints(txns, account_id=1)
    existing_fps = {fingerprints[0]}

    new_txns, new_fps, flagged = deduplicate(txns, fingerprints, existing_fps)
    assert len(new_txns) == 1
    assert new_txns[0]["raw_description"] == "COFFEE SHOP"
    assert len(flagged) == 0


def test_deduplicate_flags_ambiguous():
    """When existing has 1 copy but import has 2 identical, flag the second."""
    txns = [
        ParsedTransaction(date=date(2024, 1, 16), amount=Decimal("-5.00"), raw_description="COFFEE SHOP"),
        ParsedTransaction(date=date(2024, 1, 16), amount=Decimal("-5.00"), raw_description="COFFEE SHOP"),
    ]
    fingerprints = compute_fingerprints(txns, account_id=1)
    # Existing DB has sequence 0 but not sequence 1
    existing_fps = {fingerprints[0]}

    new_txns, new_fps, flagged = deduplicate(txns, fingerprints, existing_fps)
    assert len(new_txns) == 1
    assert len(flagged) == 1
    assert flagged[0]["raw_description"] == "COFFEE SHOP"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_importer/test_dedup.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement dedup**

`spending/importer/dedup.py`:
```python
import hashlib
from collections import Counter

from spending.types import ParsedTransaction


def _base_key(txn: ParsedTransaction, account_id: int) -> str:
    return f"{txn['date'].isoformat()}|{txn['amount']}|{txn['raw_description']}|{account_id}"


def _fingerprint(base_key: str, seq: int) -> str:
    return hashlib.sha256(f"{base_key}|{seq}".encode()).hexdigest()


def compute_fingerprints(
    txns: list[ParsedTransaction], account_id: int
) -> list[str]:
    counts: Counter[str] = Counter()
    fingerprints: list[str] = []

    for txn in txns:
        key = _base_key(txn, account_id)
        seq = counts[key]
        counts[key] += 1
        fingerprints.append(_fingerprint(key, seq))

    return fingerprints


def deduplicate(
    txns: list[ParsedTransaction],
    fingerprints: list[str],
    existing_fingerprints: set[str],
    account_id: int,
) -> tuple[list[ParsedTransaction], list[str], list[ParsedTransaction]]:
    """Returns (new_transactions, new_fingerprints, flagged_transactions).

    - Exact fingerprint match with existing: skipped (auto-dedup).
    - Sequence > 0 whose seq-0 sibling is in existing: flagged as ambiguous.
    - Everything else: new.
    """
    new_txns: list[ParsedTransaction] = []
    new_fps: list[str] = []
    flagged: list[ParsedTransaction] = []

    for txn, fp in zip(txns, fingerprints):
        if fp in existing_fingerprints:
            continue

        key = _base_key(txn, account_id)
        seq0_fp = _fingerprint(key, 0)

        if seq0_fp in existing_fingerprints:
            flagged.append(txn)
        else:
            new_txns.append(txn)
            new_fps.append(fp)

    return new_txns, new_fps, flagged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_importer/test_dedup.py -v`
Expected: 5 tests PASS

Note: the `deduplicate` tests must pass `account_id=1`:
```python
new_txns, new_fps, flagged = deduplicate(txns, fingerprints, existing_fps, account_id=1)
```

- [ ] **Step 5: Commit**

```bash
git add spending/importer/dedup.py tests/test_importer/test_dedup.py
git commit -m "feat: sequence-aware fingerprinting and deduplication"
```

---

### Task 10: Import Orchestration

**Files:**
- Create: `spending/repository/imports.py`
- Create: `tests/test_repository/test_imports.py`
- Modify: `spending/importer/__init__.py`
- Modify: `spending/repository/__init__.py`

- [ ] **Step 1: Write imports repository tests**

`tests/test_repository/test_imports.py`:
```python
from datetime import date
from decimal import Decimal

from spending.repository.accounts import add_account
from spending.repository.imports import (
    check_file_hash,
    confirm_import,
    create_import,
    get_existing_fingerprints,
    get_staging_imports,
    insert_transactions,
    reject_import,
)


def test_create_import(conn):
    acct_id = add_account(conn, name="Chase", institution="Chase", account_type="credit_card")
    imp_id = create_import(conn, account_id=acct_id, filename="test.ofx", file_hash="abc123")
    assert imp_id > 0


def test_check_file_hash_not_exists(conn):
    assert check_file_hash(conn, "abc123") is False


def test_check_file_hash_exists(conn):
    acct_id = add_account(conn, name="Chase", institution="Chase", account_type="credit_card")
    create_import(conn, account_id=acct_id, filename="test.ofx", file_hash="abc123")
    assert check_file_hash(conn, "abc123") is True


def test_insert_and_get_transactions(conn):
    acct_id = add_account(conn, name="Chase", institution="Chase", account_type="credit_card")
    imp_id = create_import(conn, account_id=acct_id, filename="test.ofx", file_hash="abc123")
    insert_transactions(
        conn,
        import_id=imp_id,
        account_id=acct_id,
        transactions=[
            {
                "date": date(2024, 1, 15),
                "amount": Decimal("-42.50"),
                "raw_description": "WHOLE FOODS #1234",
                "normalized_merchant": "WHOLE FOODS",
                "fingerprint": "fp1",
            }
        ],
    )
    fps = get_existing_fingerprints(conn, acct_id)
    assert "fp1" in fps


def test_confirm_import(conn):
    acct_id = add_account(conn, name="Chase", institution="Chase", account_type="credit_card")
    imp_id = create_import(conn, account_id=acct_id, filename="test.ofx", file_hash="abc123")
    confirm_import(conn, imp_id)
    staging = get_staging_imports(conn)
    assert len(staging) == 0


def test_reject_import(conn):
    acct_id = add_account(conn, name="Chase", institution="Chase", account_type="credit_card")
    imp_id = create_import(conn, account_id=acct_id, filename="test.ofx", file_hash="abc123")
    reject_import(conn, imp_id)
    staging = get_staging_imports(conn)
    assert len(staging) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_repository/test_imports.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement imports repository**

`spending/repository/imports.py`:
```python
import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, insert, select, update

from spending.models import imports, transactions


def compute_file_hash(file_path: str | Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def check_file_hash(conn: Connection, file_hash: str) -> bool:
    row = conn.execute(
        select(imports.c.id).where(imports.c.file_hash == file_hash)
    ).fetchone()
    return row is not None


def create_import(
    conn: Connection, *, account_id: int, filename: str, file_hash: str
) -> int:
    result = conn.execute(
        insert(imports).values(
            account_id=account_id, filename=filename, file_hash=file_hash
        )
    )
    conn.commit()
    return result.inserted_primary_key[0]


def insert_transactions(
    conn: Connection,
    *,
    import_id: int,
    account_id: int,
    transactions_data: list[dict],
) -> None:
    for txn in transactions_data:
        conn.execute(
            insert(transactions).values(
                import_id=import_id,
                account_id=account_id,
                date=txn["date"],
                amount=txn["amount"],
                raw_description=txn["raw_description"],
                normalized_merchant=txn["normalized_merchant"],
                fingerprint=txn["fingerprint"],
            )
        )
    conn.commit()


def get_existing_fingerprints(conn: Connection, account_id: int) -> set[str]:
    rows = conn.execute(
        select(transactions.c.fingerprint).where(
            transactions.c.account_id == account_id
        )
    ).fetchall()
    return {row[0] for row in rows}


def get_staging_imports(conn: Connection) -> list[dict]:
    rows = conn.execute(
        select(imports).where(imports.c.status == "staging").order_by(imports.c.imported_at.desc())
    ).fetchall()
    return [dict(row._mapping) for row in rows]


def confirm_import(conn: Connection, import_id: int) -> None:
    conn.execute(
        update(imports).where(imports.c.id == import_id).values(status="confirmed")
    )
    conn.commit()


def reject_import(conn: Connection, import_id: int) -> None:
    conn.execute(
        update(imports).where(imports.c.id == import_id).values(status="rejected")
    )
    conn.commit()
```

- [ ] **Step 4: Fix test to use correct parameter name**

Update `test_insert_and_get_transactions` to use `transactions_data=` instead of `transactions=`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_repository/test_imports.py -v`
Expected: 6 tests PASS

- [ ] **Step 6: Implement import orchestrator**

`spending/importer/__init__.py`:
```python
from pathlib import Path

from sqlalchemy import Connection

from spending.importer.csv_parser import detect_institution_config, parse_csv
from spending.importer.dedup import compute_fingerprints, deduplicate
from spending.importer.normalize import normalize_merchant
from spending.importer.ofx import parse_ofx
from spending.repository.imports import (
    check_file_hash,
    compute_file_hash,
    create_import,
    get_existing_fingerprints,
    insert_transactions,
)
from spending.types import ParsedTransaction


def run_import(
    conn: Connection,
    file_path: str | Path,
    account_id: int,
    configs_dir: str = "configs/institutions",
) -> dict:
    """Run the full import pipeline for a single file.

    Returns a dict with keys: import_id, new_count, skipped_count,
    flagged_count, new_merchants.
    """
    file_path = Path(file_path)

    # Check for exact re-import
    file_hash = compute_file_hash(file_path)
    if check_file_hash(conn, file_hash):
        return {
            "import_id": None,
            "new_count": 0,
            "skipped_count": 0,
            "flagged_count": 0,
            "new_merchants": [],
            "error": f"File already imported: {file_path.name}",
        }

    # Parse based on format
    suffix = file_path.suffix.lower()
    if suffix in (".ofx", ".qfx"):
        result = parse_ofx(file_path)
    elif suffix == ".csv":
        config_path = detect_institution_config(file_path, configs_dir)
        if config_path is None:
            return {
                "import_id": None,
                "new_count": 0,
                "skipped_count": 0,
                "flagged_count": 0,
                "new_merchants": [],
                "error": f"No institution config matches: {file_path.name}",
            }
        result = parse_csv(file_path, config_path)
    else:
        return {
            "import_id": None,
            "new_count": 0,
            "skipped_count": 0,
            "flagged_count": 0,
            "new_merchants": [],
            "error": f"Unsupported format: {suffix}",
        }

    if not result["transactions"]:
        return {
            "import_id": None,
            "new_count": 0,
            "skipped_count": 0,
            "flagged_count": 0,
            "new_merchants": [],
            "error": "No transactions found in file",
        }

    # Normalize merchant names
    for txn in result["transactions"]:
        txn["normalized_merchant"] = normalize_merchant(txn["raw_description"])

    # Fingerprint and dedup
    fingerprints = compute_fingerprints(result["transactions"], account_id)
    existing_fps = get_existing_fingerprints(conn, account_id)
    new_txns, new_fps, flagged = deduplicate(
        result["transactions"], fingerprints, existing_fps, account_id
    )

    skipped_count = len(result["transactions"]) - len(new_txns) - len(flagged)

    # Create import record
    import_id = create_import(
        conn,
        account_id=account_id,
        filename=file_path.name,
        file_hash=file_hash,
    )

    # Build transaction records with fingerprints
    txn_records = []
    for txn, fp in zip(new_txns, new_fps):
        txn_records.append(
            {
                "date": txn["date"],
                "amount": txn["amount"],
                "raw_description": txn["raw_description"],
                "normalized_merchant": txn["normalized_merchant"],
                "fingerprint": fp,
            }
        )

    # Insert flagged transactions too (they'll be reviewed in staging)
    flagged_fps = compute_fingerprints(flagged, account_id)
    for txn, fp in zip(flagged, flagged_fps):
        txn_records.append(
            {
                "date": txn["date"],
                "amount": txn["amount"],
                "raw_description": txn["raw_description"],
                "normalized_merchant": txn["normalized_merchant"],
                "fingerprint": fp,
            }
        )

    if txn_records:
        insert_transactions(
            conn,
            import_id=import_id,
            account_id=account_id,
            transactions_data=txn_records,
        )

    # Collect new merchant names
    new_merchants = list(
        {txn["normalized_merchant"] for txn in new_txns + flagged}
    )

    return {
        "import_id": import_id,
        "new_count": len(new_txns),
        "skipped_count": skipped_count,
        "flagged_count": len(flagged),
        "new_merchants": new_merchants,
    }
```

- [ ] **Step 7: Update repository __init__.py with imports**

Add imports from `spending.repository.imports` to `spending/repository/__init__.py`.

- [ ] **Step 8: Run all importer tests**

Run: `uv run pytest tests/test_importer/ tests/test_repository/test_imports.py -v`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add spending/importer/ spending/repository/imports.py tests/test_repository/test_imports.py
git commit -m "feat: import orchestration with parse, normalize, dedup pipeline"
```

---

### Task 11: Claude API Classifier

**Files:**
- Create: `spending/classifier.py`
- Create: `spending/repository/merchants.py`
- Create: `tests/test_classifier.py`
- Create: `tests/test_repository/test_merchants.py`
- Modify: `spending/repository/__init__.py`

- [ ] **Step 1: Write merchant cache repository tests**

`tests/test_repository/test_merchants.py`:
```python
from spending.repository.merchants import (
    get_cached_category,
    get_uncached_merchants,
    list_merchants,
    set_merchant_category,
)


def test_set_and_get_cached_category(conn):
    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    assert get_cached_category(conn, "WHOLE FOODS") == "Groceries"


def test_get_cached_category_miss(conn):
    assert get_cached_category(conn, "UNKNOWN") is None


def test_get_uncached_merchants(conn):
    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    uncached = get_uncached_merchants(conn, ["WHOLE FOODS", "NETFLIX", "TARGET"])
    assert set(uncached) == {"NETFLIX", "TARGET"}


def test_set_merchant_category_updates_existing(conn):
    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    set_merchant_category(conn, "WHOLE FOODS", "Shopping", source="manual")
    assert get_cached_category(conn, "WHOLE FOODS") == "Shopping"


def test_list_merchants(conn):
    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    set_merchant_category(conn, "NETFLIX", "Subscriptions", source="manual")
    merchants = list_merchants(conn)
    assert len(merchants) == 2
    names = {m["merchant_name"] for m in merchants}
    assert names == {"WHOLE FOODS", "NETFLIX"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_repository/test_merchants.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement merchant cache repository**

`spending/repository/merchants.py`:
```python
from datetime import datetime, timezone

from sqlalchemy import Connection, insert, select, update

from spending.models import merchant_cache


def get_cached_category(conn: Connection, merchant_name: str) -> str | None:
    row = conn.execute(
        select(merchant_cache.c.category).where(
            merchant_cache.c.merchant_name == merchant_name
        )
    ).fetchone()
    return row[0] if row else None


def set_merchant_category(
    conn: Connection, merchant_name: str, category: str, *, source: str
) -> None:
    existing = conn.execute(
        select(merchant_cache.c.id).where(
            merchant_cache.c.merchant_name == merchant_name
        )
    ).fetchone()

    if existing:
        conn.execute(
            update(merchant_cache)
            .where(merchant_cache.c.merchant_name == merchant_name)
            .values(
                category=category,
                source=source,
                updated_at=datetime.now(timezone.utc),
            )
        )
    else:
        conn.execute(
            insert(merchant_cache).values(
                merchant_name=merchant_name, category=category, source=source
            )
        )
    conn.commit()


def get_uncached_merchants(
    conn: Connection, merchant_names: list[str]
) -> list[str]:
    if not merchant_names:
        return []
    cached = conn.execute(
        select(merchant_cache.c.merchant_name).where(
            merchant_cache.c.merchant_name.in_(merchant_names)
        )
    ).fetchall()
    cached_set = {row[0] for row in cached}
    return [name for name in merchant_names if name not in cached_set]


def list_merchants(conn: Connection) -> list[dict]:
    rows = conn.execute(
        select(merchant_cache).order_by(merchant_cache.c.merchant_name)
    ).fetchall()
    return [dict(row._mapping) for row in rows]
```

- [ ] **Step 4: Run merchant tests to verify they pass**

Run: `uv run pytest tests/test_repository/test_merchants.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Write classifier tests**

`tests/test_classifier.py`:
```python
from unittest.mock import MagicMock, patch

from spending.classifier import classify_merchants, _build_prompt


def test_build_prompt():
    prompt = _build_prompt(
        merchant_names=["WHOLE FOODS", "NETFLIX"],
        category_names=["Groceries", "Subscriptions", "Other"],
    )
    assert "WHOLE FOODS" in prompt
    assert "NETFLIX" in prompt
    assert "Groceries" in prompt
    assert "JSON" in prompt


def test_classify_merchants_returns_mapping():
    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(text='[{"merchant_name": "WHOLE FOODS", "category": "Groceries"}, {"merchant_name": "NETFLIX", "category": "Subscriptions"}]')
    ]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("spending.classifier.Anthropic", return_value=mock_client):
        result = classify_merchants(
            merchant_names=["WHOLE FOODS", "NETFLIX"],
            category_names=["Groceries", "Subscriptions", "Other"],
        )

    assert result == {"WHOLE FOODS": "Groceries", "NETFLIX": "Subscriptions"}


def test_classify_merchants_empty_list():
    result = classify_merchants(merchant_names=[], category_names=["Groceries"])
    assert result == {}
```

- [ ] **Step 6: Run classifier tests to verify they fail**

Run: `uv run pytest tests/test_classifier.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 7: Implement classifier**

`spending/classifier.py`:
```python
import json
import logging

from anthropic import Anthropic

logger = logging.getLogger(__name__)


def _build_prompt(merchant_names: list[str], category_names: list[str]) -> str:
    categories_str = ", ".join(category_names)
    merchants_str = "\n".join(f"- {name}" for name in merchant_names)

    return f"""Classify each merchant name into exactly one spending category.

Categories: {categories_str}

Merchant names:
{merchants_str}

Respond with ONLY a JSON array. Each element must have "merchant_name" (exactly as given) and "category" (from the list above). Example:
[{{"merchant_name": "WHOLE FOODS", "category": "Groceries"}}]"""


def classify_merchants(
    merchant_names: list[str],
    category_names: list[str],
) -> dict[str, str]:
    """Classify merchant names via Claude API. Returns {merchant_name: category}."""
    if not merchant_names:
        return {}

    client = Anthropic()
    prompt = _build_prompt(merchant_names, category_names)

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        classifications = json.loads(text)

        result = {}
        valid_categories = set(category_names)
        for item in classifications:
            name = item["merchant_name"]
            category = item["category"]
            if category in valid_categories:
                result[name] = category
            else:
                logger.warning(
                    f"Invalid category '{category}' for '{name}', skipping"
                )

        return result

    except Exception:
        logger.exception("Classification API call failed")
        return {}
```

- [ ] **Step 8: Run all tests**

Run: `uv run pytest tests/test_classifier.py tests/test_repository/test_merchants.py -v`
Expected: 8 tests PASS

- [ ] **Step 9: Update repository __init__.py**

Add merchants imports to `spending/repository/__init__.py`.

- [ ] **Step 10: Commit**

```bash
git add spending/classifier.py spending/repository/merchants.py tests/test_classifier.py tests/test_repository/test_merchants.py spending/repository/__init__.py
git commit -m "feat: Claude API classifier with merchant cache repository"
```

---

### Task 12: CLI Import Command

**Files:**
- Modify: `spending/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write CLI import tests**

Add to `tests/test_cli.py`:
```python
from unittest.mock import patch


def test_import_ofx(tmp_path, sample_ofx):
    db = tmp_path / "test.db"
    runner = CliRunner()
    # Create account first
    runner.invoke(cli, [
        "--db", str(db), "accounts", "add",
        "--name", "Test Account", "--institution", "Test", "--type", "checking",
    ])

    with patch("spending.importer.classify_merchants", return_value={}):
        result = runner.invoke(cli, [
            "--db", str(db), "import", str(sample_ofx), "--account", "Test Account",
        ])

    assert result.exit_code == 0
    assert "imported" in result.output.lower() or "transaction" in result.output.lower()
```

Note: Add the `sample_ofx` fixture from `tests/test_importer/conftest.py` to `tests/conftest.py` or import it.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_import_ofx -v`
Expected: FAIL — no `import` command

- [ ] **Step 3: Add import command to CLI**

Add to `spending/cli.py`:
```python
import os
from pathlib import Path

from spending.importer import run_import
from spending.classifier import classify_merchants
from spending.repository.accounts import get_account_by_name
from spending.repository.categories import get_category_names
from spending.repository.merchants import get_uncached_merchants, set_merchant_category


@cli.command("import")
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--account", help="Account name to import into")
@click.pass_context
def import_cmd(ctx, files, account):
    """Import statement files."""
    engine = ctx.obj["engine"]

    # Expand directories
    file_paths = []
    for f in files:
        p = Path(f)
        if p.is_dir():
            file_paths.extend(p.glob("*.ofx"))
            file_paths.extend(p.glob("*.qfx"))
            file_paths.extend(p.glob("*.csv"))
        else:
            file_paths.append(p)

    if not file_paths:
        click.echo("No supported files found.")
        return

    with engine.connect() as conn:
        # Resolve account
        if account:
            acct = get_account_by_name(conn, account)
            if not acct:
                click.echo(f"Account not found: {account}")
                return
            account_id = acct["id"]
        else:
            click.echo("--account is required (auto-detection not yet implemented)")
            return

        all_new_merchants = set()
        for fp in file_paths:
            click.echo(f"Importing {fp.name}...")
            result = run_import(conn, fp, account_id)

            if result.get("error"):
                click.echo(f"  Error: {result['error']}")
                continue

            click.echo(
                f"  {result['new_count']} new, "
                f"{result['skipped_count']} skipped, "
                f"{result['flagged_count']} flagged"
            )
            all_new_merchants.update(result["new_merchants"])

        # Classify new merchants
        if all_new_merchants:
            uncached = get_uncached_merchants(conn, list(all_new_merchants))
            if uncached:
                click.echo(f"Classifying {len(uncached)} new merchants...")
                category_names = get_category_names(conn)
                classifications = classify_merchants(uncached, category_names)
                for name, category in classifications.items():
                    set_merchant_category(conn, name, category, source="api")
                unclassified = len(uncached) - len(classifications)
                if unclassified:
                    click.echo(f"  {unclassified} merchants could not be classified")

        click.echo("Done. Review staged imports in the web UI.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add spending/cli.py tests/test_cli.py
git commit -m "feat: CLI import command with full pipeline"
```

---

### Task 13: CLI Status Command

**Files:**
- Create: `spending/repository/aggregations.py`
- Modify: `spending/cli.py`
- Create: `tests/test_repository/test_aggregations.py`

- [ ] **Step 1: Write aggregation tests**

`tests/test_repository/test_aggregations.py`:
```python
from datetime import date
from decimal import Decimal

from spending.repository.accounts import add_account
from spending.repository.aggregations import get_monthly_category_totals
from spending.repository.imports import create_import, insert_transactions
from spending.repository.merchants import set_merchant_category


def _seed_transactions(conn):
    """Helper: create account, import, and sample transactions."""
    acct_id = add_account(conn, name="Chase", institution="Chase", account_type="credit_card")
    imp_id = create_import(conn, account_id=acct_id, filename="test.ofx", file_hash="abc")

    # Confirm the import so transactions appear in reports
    from spending.repository.imports import confirm_import
    confirm_import(conn, imp_id)

    insert_transactions(
        conn,
        import_id=imp_id,
        account_id=acct_id,
        transactions_data=[
            {"date": date(2024, 1, 15), "amount": Decimal("-42.50"), "raw_description": "WHOLE FOODS #1234", "normalized_merchant": "WHOLE FOODS", "fingerprint": "fp1"},
            {"date": date(2024, 1, 16), "amount": Decimal("-15.00"), "raw_description": "WHOLE FOODS #5678", "normalized_merchant": "WHOLE FOODS", "fingerprint": "fp2"},
            {"date": date(2024, 1, 20), "amount": Decimal("-12.99"), "raw_description": "NETFLIX", "normalized_merchant": "NETFLIX", "fingerprint": "fp3"},
            {"date": date(2024, 2, 10), "amount": Decimal("-50.00"), "raw_description": "WHOLE FOODS #9999", "normalized_merchant": "WHOLE FOODS", "fingerprint": "fp4"},
        ],
    )
    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    set_merchant_category(conn, "NETFLIX", "Subscriptions", source="api")
    return acct_id


def test_monthly_category_totals(conn):
    _seed_transactions(conn)
    totals = get_monthly_category_totals(conn, year=2024, month=1)
    # Should have Groceries: -57.50, Subscriptions: -12.99
    by_cat = {row["category"]: row["total"] for row in totals}
    assert by_cat["Groceries"] == Decimal("-57.50")
    assert by_cat["Subscriptions"] == Decimal("-12.99")


def test_monthly_category_totals_different_month(conn):
    _seed_transactions(conn)
    totals = get_monthly_category_totals(conn, year=2024, month=2)
    by_cat = {row["category"]: row["total"] for row in totals}
    assert by_cat["Groceries"] == Decimal("-50.00")
    assert "Subscriptions" not in by_cat
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_repository/test_aggregations.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement aggregations repository**

`spending/repository/aggregations.py`:
```python
from calendar import monthrange
from datetime import date
from decimal import Decimal

from sqlalchemy import Connection, case, coalesce, extract, func, select

from spending.models import (
    imports,
    merchant_cache,
    transaction_corrections,
    transactions,
)


def _resolved_category():
    """SQL expression for the resolved category."""
    return coalesce(
        transaction_corrections.c.category,
        merchant_cache.c.category,
        "Uncategorized",
    ).label("category")


def _resolved_merchant():
    """SQL expression for the resolved merchant name."""
    return coalesce(
        transaction_corrections.c.merchant_name,
        transactions.c.normalized_merchant,
    ).label("merchant")


def _base_query():
    """Base query joining transactions with corrections and merchant cache.

    Only includes confirmed imports.
    """
    return (
        select(
            transactions.c.id,
            transactions.c.date,
            transactions.c.amount,
            transactions.c.raw_description,
            transactions.c.account_id,
            transactions.c.import_id,
            _resolved_merchant(),
            _resolved_category(),
            transaction_corrections.c.id.label("correction_id"),
        )
        .select_from(
            transactions.join(imports, transactions.c.import_id == imports.c.id)
        )
        .outerjoin(
            transaction_corrections,
            transactions.c.id == transaction_corrections.c.transaction_id,
        )
        .outerjoin(
            merchant_cache,
            coalesce(
                transaction_corrections.c.merchant_name,
                transactions.c.normalized_merchant,
            )
            == merchant_cache.c.merchant_name,
        )
        .where(imports.c.status == "confirmed")
    )


def get_monthly_category_totals(
    conn: Connection, *, year: int, month: int
) -> list[dict]:
    start = date(year, month, 1)
    _, last_day = monthrange(year, month)
    end = date(year, month, last_day)

    subq = _base_query().where(
        transactions.c.date >= start,
        transactions.c.date <= end,
    ).subquery()

    stmt = (
        select(
            subq.c.category,
            func.count().label("count"),
            func.sum(subq.c.amount).label("total"),
        )
        .group_by(subq.c.category)
        .order_by(func.sum(subq.c.amount))
    )

    rows = conn.execute(stmt).fetchall()
    return [
        {"category": row[0], "count": row[1], "total": row[2]}
        for row in rows
    ]


def get_monthly_totals_range(
    conn: Connection, *, start_date: date, end_date: date
) -> list[dict]:
    """Get category totals per month for a date range. Returns list of
    {year, month, category, total} dicts."""
    subq = _base_query().where(
        transactions.c.date >= start_date,
        transactions.c.date <= end_date,
    ).subquery()

    stmt = (
        select(
            extract("year", subq.c.date).label("year"),
            extract("month", subq.c.date).label("month"),
            subq.c.category,
            func.sum(subq.c.amount).label("total"),
        )
        .group_by("year", "month", subq.c.category)
        .order_by("year", "month")
    )

    rows = conn.execute(stmt).fetchall()
    return [
        {"year": int(row[0]), "month": int(row[1]), "category": row[2], "total": row[3]}
        for row in rows
    ]


def get_rolling_average(
    conn: Connection, *, year: int, month: int, months_back: int = 3
) -> dict[str, Decimal]:
    """Get trailing N-month average per category."""
    # Calculate start date for the rolling window
    start_month = month - months_back
    start_year = year
    while start_month <= 0:
        start_month += 12
        start_year -= 1

    start = date(start_year, start_month, 1)
    _, last_day = monthrange(year, month)
    end = date(year, month, last_day)

    subq = _base_query().where(
        transactions.c.date >= start,
        transactions.c.date < date(year, month, 1),  # exclude current month
    ).subquery()

    stmt = (
        select(
            subq.c.category,
            (func.sum(subq.c.amount) / months_back).label("avg"),
        )
        .group_by(subq.c.category)
    )

    rows = conn.execute(stmt).fetchall()
    return {row[0]: row[1] for row in rows}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_repository/test_aggregations.py -v`
Expected: 2 tests PASS

- [ ] **Step 5: Add status command to CLI**

Add to `spending/cli.py`:
```python
from datetime import date as date_today
from spending.repository.aggregations import get_monthly_category_totals
from spending.repository.imports import get_staging_imports


@cli.command()
@click.pass_context
def status(ctx):
    """Show current month spending summary."""
    engine = ctx.obj["engine"]
    today = date_today.today()

    with engine.connect() as conn:
        totals = get_monthly_category_totals(conn, year=today.year, month=today.month)
        staging = get_staging_imports(conn)

    if not totals:
        click.echo(f"No spending data for {today.strftime('%B %Y')}.")
    else:
        grand_total = sum(row["total"] for row in totals)
        click.echo(f"\n{today.strftime('%B %Y')} Spending: ${abs(grand_total):,.2f}")
        click.echo("-" * 40)
        for row in totals[:5]:
            click.echo(f"  {row['category']:20s} ${abs(row['total']):>10,.2f}  ({row['count']} txns)")
        if len(totals) > 5:
            click.echo(f"  ... and {len(totals) - 5} more categories")

    if staging:
        click.echo(f"\n{len(staging)} pending import(s) awaiting review.")


@cli.command()
@click.option("--port", default=5002, type=int)
def serve(port):
    """Start the web server."""
    from web.app import create_app

    app = create_app()
    app.run(debug=True, port=port)
```

- [ ] **Step 6: Run all CLI tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add spending/repository/aggregations.py spending/cli.py tests/test_repository/test_aggregations.py spending/repository/__init__.py
git commit -m "feat: aggregation queries and CLI status command"
```

---

### Task 14: Web Foundation

**Files:**
- Create: `web/app.py`
- Create: `web/routes/__init__.py`
- Create: `web/static/htmx.min.js` (download)
- Create: `web/static/app.js`
- Create: `web/templates/base.html`

- [ ] **Step 1: Download HTMX**

Run: `curl -o web/static/htmx.min.js https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js`
Expected: File downloaded to `web/static/`

- [ ] **Step 2: Create Flask app factory**

`web/app.py`:
```python
import os
from pathlib import Path

from flask import Flask
from sqlalchemy import create_engine

from spending.db import init_db
from spending.models import metadata
from spending.repository.categories import seed_categories


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(__name__)

    if db_path is None:
        db_path = os.environ.get("SPENDING_DB", "spending.db")

    engine = create_engine(f"sqlite:///{db_path}")
    metadata.create_all(engine)
    app.config["engine"] = engine

    with engine.connect() as conn:
        seed_categories(conn, "configs/categories.yaml")

    from web.routes import register_blueprints

    register_blueprints(app)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5002)
```

- [ ] **Step 3: Create route registration**

`web/routes/__init__.py`:
```python
from flask import Flask


def register_blueprints(app: Flask) -> None:
    from web.routes.monthly import bp as monthly_bp
    from web.routes.transactions import bp as transactions_bp
    from web.routes.trends import bp as trends_bp
    from web.routes.merchants import bp as merchants_bp
    from web.routes.imports import bp as imports_bp

    app.register_blueprint(monthly_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(trends_bp)
    app.register_blueprint(merchants_bp)
    app.register_blueprint(imports_bp)
```

- [ ] **Step 4: Create base template**

`web/templates/base.html`:
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Spending Tracker</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="{{ url_for('static', filename='htmx.min.js') }}"></script>
</head>
<body class="bg-gray-50 min-h-screen">
    <header class="bg-white shadow-sm border-b">
        <div class="max-w-7xl mx-auto px-4 py-3">
            <h1 class="text-xl font-semibold text-gray-900">Spending Tracker</h1>
        </div>
    </header>

    <nav class="bg-white border-b">
        <div class="max-w-7xl mx-auto px-4">
            <div class="flex space-x-1" role="tablist">
                {% set tabs = [
                    ("monthly", "/monthly", "Monthly"),
                    ("transactions", "/transactions", "Transactions"),
                    ("trends", "/trends", "Trends"),
                    ("merchants", "/merchants", "Merchants"),
                    ("import", "/import", "Import"),
                ] %}
                {% for tab_id, tab_url, tab_label in tabs %}
                <a href="{{ tab_url }}"
                   hx-get="{{ tab_url }}"
                   hx-target="#content"
                   hx-push-url="true"
                   class="px-4 py-3 text-sm font-medium border-b-2 transition-colors
                          {% if active_tab == tab_id %}
                          border-blue-500 text-blue-600
                          {% else %}
                          border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300
                          {% endif %}"
                   role="tab">
                    {{ tab_label }}
                </a>
                {% endfor %}
            </div>
        </div>
    </nav>

    <main class="max-w-7xl mx-auto px-4 py-6" id="content">
        {% block content %}{% endblock %}
    </main>

    <script src="{{ url_for('static', filename='app.js') }}"></script>
</body>
</html>
```

- [ ] **Step 5: Create stub route files**

Create stub blueprints for each tab so the app starts. Each will be fully implemented in later tasks.

`web/routes/monthly.py`:
```python
from flask import Blueprint, render_template

bp = Blueprint("monthly", __name__)


@bp.route("/")
@bp.route("/monthly")
def index():
    return render_template("monthly.html", active_tab="monthly")
```

`web/routes/transactions.py`:
```python
from flask import Blueprint, render_template

bp = Blueprint("transactions", __name__)


@bp.route("/transactions")
def index():
    return render_template("transactions.html", active_tab="transactions")
```

`web/routes/trends.py`:
```python
from flask import Blueprint, render_template

bp = Blueprint("trends", __name__)


@bp.route("/trends")
def index():
    return render_template("trends.html", active_tab="trends")
```

`web/routes/merchants.py`:
```python
from flask import Blueprint, render_template

bp = Blueprint("merchants", __name__)


@bp.route("/merchants")
def index():
    return render_template("merchants.html", active_tab="merchants")
```

`web/routes/imports.py`:
```python
from flask import Blueprint, render_template

bp = Blueprint("imports", __name__)


@bp.route("/import")
def index():
    return render_template("import.html", active_tab="import")
```

Create stub templates for each (all extend base.html):

`web/templates/monthly.html`:
```html
{% extends "base.html" %}
{% block content %}
<h2 class="text-lg font-semibold mb-4">Monthly Summary</h2>
<p class="text-gray-500">Coming soon...</p>
{% endblock %}
```

(Repeat similar stubs for `transactions.html`, `trends.html`, `merchants.html`, `import.html` — just change the heading text.)

- [ ] **Step 6: Create app.js stub**

`web/static/app.js`:
```javascript
// Spending Tracker - client-side behavior
document.addEventListener('DOMContentLoaded', function() {
    // HTMX will handle most interactions
});
```

- [ ] **Step 7: Verify the app starts**

Run: `uv run python web/app.py`
Expected: Flask dev server starts on port 5002, all 5 tab URLs respond with HTML

- [ ] **Step 8: Commit**

```bash
git add web/ 
git commit -m "feat: Flask app with tab navigation and HTMX foundation"
```

---

### Task 15: Monthly Tab

**Files:**
- Create: `spending/repository/transactions.py`
- Modify: `web/routes/monthly.py`
- Modify: `web/templates/monthly.html`
- Create: `web/templates/partials/monthly_table.html`
- Create: `web/templates/partials/monthly_drilldown.html`
- Create: `tests/test_repository/test_transactions.py`

- [ ] **Step 1: Write transaction query tests**

`tests/test_repository/test_transactions.py`:
```python
from datetime import date
from decimal import Decimal

from spending.repository.accounts import add_account
from spending.repository.imports import confirm_import, create_import, insert_transactions
from spending.repository.merchants import set_merchant_category
from spending.repository.transactions import get_transactions


def _seed(conn):
    acct_id = add_account(conn, name="Chase", institution="Chase", account_type="credit_card")
    imp_id = create_import(conn, account_id=acct_id, filename="t.ofx", file_hash="h1")
    confirm_import(conn, imp_id)
    insert_transactions(
        conn,
        import_id=imp_id,
        account_id=acct_id,
        transactions_data=[
            {"date": date(2024, 1, 15), "amount": Decimal("-42.50"), "raw_description": "WHOLE FOODS #1234", "normalized_merchant": "WHOLE FOODS", "fingerprint": "fp1"},
            {"date": date(2024, 1, 20), "amount": Decimal("-12.99"), "raw_description": "NETFLIX.COM", "normalized_merchant": "NETFLIX", "fingerprint": "fp2"},
        ],
    )
    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    set_merchant_category(conn, "NETFLIX", "Subscriptions", source="api")
    return acct_id


def test_get_transactions_returns_resolved_fields(conn):
    _seed(conn)
    txns = get_transactions(conn, year=2024, month=1)
    assert len(txns) == 2
    wf = next(t for t in txns if t["merchant"] == "WHOLE FOODS")
    assert wf["category"] == "Groceries"


def test_get_transactions_uncategorized(conn):
    acct_id = add_account(conn, name="Chase", institution="Chase", account_type="credit_card")
    imp_id = create_import(conn, account_id=acct_id, filename="t.ofx", file_hash="h2")
    confirm_import(conn, imp_id)
    insert_transactions(
        conn,
        import_id=imp_id,
        account_id=acct_id,
        transactions_data=[
            {"date": date(2024, 1, 15), "amount": Decimal("-10.00"), "raw_description": "UNKNOWN SHOP", "normalized_merchant": "UNKNOWN SHOP", "fingerprint": "fp3"},
        ],
    )
    txns = get_transactions(conn, year=2024, month=1)
    assert txns[0]["category"] == "Uncategorized"


def test_get_transactions_filter_by_category(conn):
    _seed(conn)
    txns = get_transactions(conn, year=2024, month=1, category="Groceries")
    assert len(txns) == 1
    assert txns[0]["merchant"] == "WHOLE FOODS"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_repository/test_transactions.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement transactions repository**

`spending/repository/transactions.py`:
```python
from calendar import monthrange
from datetime import date

from sqlalchemy import Connection, select

from spending.repository.aggregations import _base_query


def get_transactions(
    conn: Connection,
    *,
    year: int | None = None,
    month: int | None = None,
    category: str | None = None,
    account_id: int | None = None,
    search: str | None = None,
    status: str | None = None,
    import_id: int | None = None,
) -> list[dict]:
    """Get transactions with resolved category and merchant.

    Filters are optional and combine with AND.
    """
    from spending.models import imports, transactions, transaction_corrections

    subq = _base_query()

    if year and month:
        start = date(year, month, 1)
        _, last_day = monthrange(year, month)
        end = date(year, month, last_day)
        subq = subq.where(
            transactions.c.date >= start,
            transactions.c.date <= end,
        )

    if account_id:
        subq = subq.where(transactions.c.account_id == account_id)

    if import_id:
        subq = subq.where(transactions.c.import_id == import_id)

    subq = subq.order_by(transactions.c.date.desc()).subquery()

    # Wrap in outer query to filter on resolved columns
    stmt = select(subq)

    if category:
        stmt = stmt.where(subq.c.category == category)

    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            subq.c.raw_description.ilike(pattern)
            | subq.c.merchant.ilike(pattern)
        )

    if status == "corrected":
        stmt = stmt.where(subq.c.correction_id.isnot(None))
    elif status == "uncategorized":
        stmt = stmt.where(subq.c.category == "Uncategorized")
    elif status == "categorized":
        stmt = stmt.where(
            subq.c.category != "Uncategorized",
            subq.c.correction_id.is_(None),
        )

    rows = conn.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_repository/test_transactions.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Implement monthly route**

`web/routes/monthly.py`:
```python
from datetime import date

from flask import Blueprint, current_app, render_template, request

from spending.repository.aggregations import (
    get_monthly_category_totals,
    get_rolling_average,
)
from spending.repository.transactions import get_transactions

bp = Blueprint("monthly", __name__)


@bp.route("/")
@bp.route("/monthly")
def index():
    today = date.today()
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        totals = get_monthly_category_totals(conn, year=year, month=month)
        averages = get_rolling_average(conn, year=year, month=month, months_back=3)

    # Merge averages into totals
    for row in totals:
        row["average"] = averages.get(row["category"])

    grand_total = sum(row["total"] for row in totals) if totals else 0

    template = "partials/monthly_table.html" if request.headers.get("HX-Request") else "monthly.html"
    return render_template(
        template,
        active_tab="monthly",
        totals=totals,
        grand_total=grand_total,
        year=year,
        month=month,
    )


@bp.route("/monthly/drilldown/<category>")
def drilldown(category):
    year = request.args.get("year", date.today().year, type=int)
    month = request.args.get("month", date.today().month, type=int)

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        txns = get_transactions(conn, year=year, month=month, category=category)

    return render_template(
        "partials/monthly_drilldown.html",
        transactions=txns,
        category=category,
    )
```

- [ ] **Step 6: Create monthly templates**

`web/templates/monthly.html`:
```html
{% extends "base.html" %}
{% block content %}
<div class="flex items-center justify-between mb-6">
    <h2 class="text-lg font-semibold">Monthly Summary</h2>
    <div class="flex items-center space-x-2">
        <a href="/monthly?year={{ year }}&month={{ month - 1 if month > 1 else 12 }}{{'&year=' ~ (year - 1) if month == 1 else ''}}"
           hx-get="/monthly?year={{ year }}&month={{ month - 1 if month > 1 else 12 }}"
           hx-target="#content" hx-push-url="true"
           class="px-2 py-1 text-gray-600 hover:bg-gray-100 rounded">&larr;</a>
        <span class="text-sm font-medium text-gray-700">
            {{ '%02d/%d' | format(month, year) }}
        </span>
        <a href="/monthly?year={{ year }}&month={{ month + 1 if month < 12 else 1 }}"
           hx-get="/monthly?year={{ year }}&month={{ month + 1 if month < 12 else 1 }}"
           hx-target="#content" hx-push-url="true"
           class="px-2 py-1 text-gray-600 hover:bg-gray-100 rounded">&rarr;</a>
    </div>
</div>
{% include "partials/monthly_table.html" %}
{% endblock %}
```

`web/templates/partials/monthly_table.html`:
```html
<table class="w-full bg-white rounded-lg shadow-sm">
    <thead>
        <tr class="border-b text-left text-sm text-gray-500">
            <th class="px-4 py-3">Category</th>
            <th class="px-4 py-3 text-right">Count</th>
            <th class="px-4 py-3 text-right">Total</th>
            <th class="px-4 py-3 text-right">3-Mo Avg</th>
            <th class="px-4 py-3 text-center">vs Avg</th>
        </tr>
    </thead>
    <tbody>
        {% for row in totals %}
        <tr class="border-b hover:bg-blue-50 cursor-pointer"
            hx-get="/monthly/drilldown/{{ row.category }}?year={{ year }}&month={{ month }}"
            hx-target="#drilldown-{{ loop.index }}"
            hx-swap="innerHTML">
            <td class="px-4 py-3 font-medium">{{ row.category }}</td>
            <td class="px-4 py-3 text-right text-sm">{{ row.count }}</td>
            <td class="px-4 py-3 text-right">${{ "%.2f"|format(row.total|abs) }}</td>
            <td class="px-4 py-3 text-right text-sm text-gray-500">
                {% if row.average %}${{ "%.2f"|format(row.average|abs) }}{% else %}&mdash;{% endif %}
            </td>
            <td class="px-4 py-3 text-center text-sm">
                {% if row.average %}
                    {% if row.total|abs > row.average|abs %}
                    <span class="text-red-500">&uarr;</span>
                    {% elif row.total|abs < row.average|abs %}
                    <span class="text-green-500">&darr;</span>
                    {% else %}
                    <span class="text-gray-400">=</span>
                    {% endif %}
                {% endif %}
            </td>
        </tr>
        <tr id="drilldown-{{ loop.index }}"></tr>
        {% endfor %}
    </tbody>
    <tfoot>
        <tr class="font-semibold">
            <td class="px-4 py-3">Total</td>
            <td></td>
            <td class="px-4 py-3 text-right">${{ "%.2f"|format(grand_total|abs) }}</td>
            <td></td>
            <td></td>
        </tr>
    </tfoot>
</table>
```

`web/templates/partials/monthly_drilldown.html`:
```html
<td colspan="5" class="px-0 py-0">
    <table class="w-full bg-gray-50">
        {% for txn in transactions %}
        <tr class="border-b border-gray-200 text-sm">
            <td class="px-8 py-2 text-gray-600">{{ txn.date }}</td>
            <td class="px-4 py-2">{{ txn.merchant }}</td>
            <td class="px-4 py-2 text-right">${{ "%.2f"|format(txn.amount|abs) }}</td>
        </tr>
        {% endfor %}
    </table>
</td>
```

- [ ] **Step 7: Verify monthly tab loads in browser**

Run: `uv run python web/app.py`
Navigate to `http://localhost:5002/monthly`
Expected: Monthly summary page renders with category table (may be empty if no data)

- [ ] **Step 8: Commit**

```bash
git add spending/repository/transactions.py tests/test_repository/test_transactions.py web/routes/monthly.py web/templates/monthly.html web/templates/partials/monthly_table.html web/templates/partials/monthly_drilldown.html
git commit -m "feat: monthly tab with category breakdown, rolling averages, and drilldown"
```

---

### Task 16: Transactions Tab

**Files:**
- Create: `spending/repository/corrections.py`
- Create: `tests/test_repository/test_corrections.py`
- Modify: `web/routes/transactions.py`
- Modify: `web/templates/transactions.html`
- Create: `web/templates/partials/transaction_rows.html`
- Create: `web/templates/partials/transaction_edit.html`

- [ ] **Step 1: Write corrections tests**

`tests/test_repository/test_corrections.py`:
```python
from datetime import date
from decimal import Decimal

from spending.repository.accounts import add_account
from spending.repository.corrections import (
    apply_transaction_correction,
    get_correction,
)
from spending.repository.imports import confirm_import, create_import, insert_transactions
from spending.repository.merchants import set_merchant_category
from spending.repository.transactions import get_transactions


def _seed(conn):
    acct_id = add_account(conn, name="Chase", institution="Chase", account_type="credit_card")
    imp_id = create_import(conn, account_id=acct_id, filename="t.ofx", file_hash="h1")
    confirm_import(conn, imp_id)
    insert_transactions(
        conn,
        import_id=imp_id,
        account_id=acct_id,
        transactions_data=[
            {"date": date(2024, 1, 15), "amount": Decimal("-42.50"), "raw_description": "WHOLE FOODS #1234", "normalized_merchant": "WHOLE FOODS", "fingerprint": "fp1"},
        ],
    )
    set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
    txns = get_transactions(conn, year=2024, month=1)
    return txns[0]["id"]


def test_apply_category_correction(conn):
    txn_id = _seed(conn)
    apply_transaction_correction(conn, txn_id, category="Shopping")
    txns = get_transactions(conn, year=2024, month=1)
    assert txns[0]["category"] == "Shopping"


def test_apply_merchant_correction(conn):
    txn_id = _seed(conn)
    apply_transaction_correction(conn, txn_id, merchant_name="WHOLE FOODS MARKET")
    txns = get_transactions(conn, year=2024, month=1)
    assert txns[0]["merchant"] == "WHOLE FOODS MARKET"


def test_get_correction(conn):
    txn_id = _seed(conn)
    apply_transaction_correction(conn, txn_id, category="Shopping", notes="Miscategorized")
    correction = get_correction(conn, txn_id)
    assert correction is not None
    assert correction["category"] == "Shopping"
    assert correction["notes"] == "Miscategorized"


def test_correction_updates_existing(conn):
    txn_id = _seed(conn)
    apply_transaction_correction(conn, txn_id, category="Shopping")
    apply_transaction_correction(conn, txn_id, category="Entertainment")
    txns = get_transactions(conn, year=2024, month=1)
    assert txns[0]["category"] == "Entertainment"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_repository/test_corrections.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement corrections repository**

`spending/repository/corrections.py`:
```python
from sqlalchemy import Connection, insert, select, update

from spending.models import transaction_corrections


def apply_transaction_correction(
    conn: Connection,
    transaction_id: int,
    *,
    category: str | None = None,
    merchant_name: str | None = None,
    notes: str | None = None,
) -> None:
    existing = conn.execute(
        select(transaction_corrections.c.id).where(
            transaction_corrections.c.transaction_id == transaction_id
        )
    ).fetchone()

    if existing:
        values = {}
        if category is not None:
            values["category"] = category
        if merchant_name is not None:
            values["merchant_name"] = merchant_name
        if notes is not None:
            values["notes"] = notes
        if values:
            conn.execute(
                update(transaction_corrections)
                .where(transaction_corrections.c.transaction_id == transaction_id)
                .values(**values)
            )
    else:
        conn.execute(
            insert(transaction_corrections).values(
                transaction_id=transaction_id,
                category=category,
                merchant_name=merchant_name,
                notes=notes,
            )
        )
    conn.commit()


def get_correction(conn: Connection, transaction_id: int) -> dict | None:
    row = conn.execute(
        select(transaction_corrections).where(
            transaction_corrections.c.transaction_id == transaction_id
        )
    ).fetchone()
    return dict(row._mapping) if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_repository/test_corrections.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Implement transactions route**

`web/routes/transactions.py`:
```python
from datetime import date

from flask import Blueprint, current_app, render_template, request

from spending.repository.accounts import list_accounts
from spending.repository.categories import get_category_names
from spending.repository.corrections import apply_transaction_correction
from spending.repository.merchants import set_merchant_category
from spending.repository.transactions import get_transactions

bp = Blueprint("transactions", __name__)


@bp.route("/transactions")
def index():
    today = date.today()
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)
    category = request.args.get("category")
    account_id = request.args.get("account_id", type=int)
    search = request.args.get("search")
    status = request.args.get("status")

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        txns = get_transactions(
            conn,
            year=year,
            month=month,
            category=category,
            account_id=account_id,
            search=search,
            status=status,
        )
        accounts = list_accounts(conn)
        categories = get_category_names(conn)

    template = "partials/transaction_rows.html" if request.headers.get("HX-Request") else "transactions.html"
    return render_template(
        template,
        active_tab="transactions",
        transactions=txns,
        accounts=accounts,
        categories=categories,
        year=year,
        month=month,
        selected_category=category,
        selected_account=account_id,
        search=search or "",
        selected_status=status,
    )


@bp.route("/transactions/<int:txn_id>/edit-category", methods=["GET"])
def edit_category_form(txn_id):
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        categories = get_category_names(conn)
    return render_template("partials/transaction_edit.html", txn_id=txn_id, categories=categories, field="category")


@bp.route("/transactions/<int:txn_id>/category", methods=["POST"])
def update_category(txn_id):
    category = request.form["category"]
    apply_to_merchant = request.form.get("apply_to_merchant") == "on"

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        if apply_to_merchant:
            # Get the merchant name and update the cache
            txns = get_transactions(conn, status=None)  # need just this txn
            from spending.models import transactions as txn_table
            from sqlalchemy import select
            row = conn.execute(
                select(txn_table.c.normalized_merchant).where(txn_table.c.id == txn_id)
            ).fetchone()
            if row:
                set_merchant_category(conn, row[0], category, source="manual")
        else:
            apply_transaction_correction(conn, txn_id, category=category)

    return render_template("partials/transaction_rows.html", transactions=[], refresh=True)


@bp.route("/transactions/bulk-categorize", methods=["POST"])
def bulk_categorize():
    txn_ids = request.form.getlist("txn_ids", type=int)
    category = request.form["category"]

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        for txn_id in txn_ids:
            apply_transaction_correction(conn, txn_id, category=category)

    return "", 200, {"HX-Trigger": "refreshTransactions"}
```

- [ ] **Step 6: Create transactions templates**

`web/templates/transactions.html`:
```html
{% extends "base.html" %}
{% block content %}
<div class="mb-4 flex flex-wrap gap-2 items-end">
    <select name="account_id" hx-get="/transactions" hx-target="#content" hx-include="[name]"
            class="border rounded px-2 py-1.5 text-sm">
        <option value="">All Accounts</option>
        {% for a in accounts %}
        <option value="{{ a.id }}" {{ 'selected' if selected_account == a.id }}>{{ a.name }}</option>
        {% endfor %}
    </select>
    <select name="category" hx-get="/transactions" hx-target="#content" hx-include="[name]"
            class="border rounded px-2 py-1.5 text-sm">
        <option value="">All Categories</option>
        {% for c in categories %}
        <option value="{{ c }}" {{ 'selected' if selected_category == c }}>{{ c }}</option>
        {% endfor %}
    </select>
    <select name="status" hx-get="/transactions" hx-target="#content" hx-include="[name]"
            class="border rounded px-2 py-1.5 text-sm">
        <option value="">All Status</option>
        <option value="categorized" {{ 'selected' if selected_status == 'categorized' }}>Categorized</option>
        <option value="uncategorized" {{ 'selected' if selected_status == 'uncategorized' }}>Uncategorized</option>
        <option value="corrected" {{ 'selected' if selected_status == 'corrected' }}>Corrected</option>
    </select>
    <input type="search" name="search" value="{{ search }}" placeholder="Search..."
           hx-get="/transactions" hx-target="#content" hx-include="[name]"
           hx-trigger="keyup changed delay:300ms"
           class="border rounded px-2 py-1.5 text-sm">
    <input type="hidden" name="year" value="{{ year }}">
    <input type="hidden" name="month" value="{{ month }}">
</div>

{% include "partials/transaction_rows.html" %}
{% endblock %}
```

`web/templates/partials/transaction_rows.html`:
```html
<table class="w-full bg-white rounded-lg shadow-sm">
    <thead>
        <tr class="border-b text-left text-sm text-gray-500">
            <th class="px-4 py-3 w-8"><input type="checkbox" id="select-all"></th>
            <th class="px-4 py-3">Date</th>
            <th class="px-4 py-3">Merchant</th>
            <th class="px-4 py-3">Description</th>
            <th class="px-4 py-3">Category</th>
            <th class="px-4 py-3 text-right">Amount</th>
        </tr>
    </thead>
    <tbody>
        {% for txn in transactions %}
        <tr class="border-b hover:bg-gray-50">
            <td class="px-4 py-3"><input type="checkbox" name="txn_ids" value="{{ txn.id }}"></td>
            <td class="px-4 py-3 text-sm">{{ txn.date }}</td>
            <td class="px-4 py-3 text-sm">{{ txn.merchant }}</td>
            <td class="px-4 py-3 text-sm text-gray-500">{{ txn.raw_description }}</td>
            <td class="px-4 py-3 text-sm cursor-pointer text-blue-600 hover:underline"
                hx-get="/transactions/{{ txn.id }}/edit-category"
                hx-target="closest tr"
                hx-swap="afterend">
                {{ txn.category }}
            </td>
            <td class="px-4 py-3 text-right">${{ "%.2f"|format(txn.amount|abs) }}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
```

`web/templates/partials/transaction_edit.html`:
```html
<tr class="bg-blue-50 border-b">
    <td colspan="6" class="px-4 py-3">
        <form hx-post="/transactions/{{ txn_id }}/category" hx-target="#content" hx-swap="innerHTML"
              class="flex items-center gap-3">
            <select name="category" class="border rounded px-2 py-1.5 text-sm">
                {% for c in categories %}
                <option value="{{ c }}">{{ c }}</option>
                {% endfor %}
            </select>
            <label class="flex items-center gap-1 text-sm">
                <input type="checkbox" name="apply_to_merchant">
                Apply to all from this merchant
            </label>
            <button type="submit" class="bg-blue-500 text-white px-3 py-1 rounded text-sm">Save</button>
            <button type="button" class="text-gray-500 text-sm"
                    onclick="this.closest('tr').remove()">Cancel</button>
        </form>
    </td>
</tr>
```

- [ ] **Step 7: Run all tests**

Run: `uv run pytest tests/ -v -m "not e2e"`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add spending/repository/corrections.py tests/test_repository/test_corrections.py web/routes/transactions.py web/templates/transactions.html web/templates/partials/transaction_rows.html web/templates/partials/transaction_edit.html
git commit -m "feat: transactions tab with filters, inline category editing, and corrections"
```

---

### Task 17: Trends Tab

**Files:**
- Modify: `web/routes/trends.py`
- Modify: `web/templates/trends.html`
- Create: `web/templates/partials/trends_table.html`
- Create: `web/templates/partials/sparkline.svg`

- [ ] **Step 1: Implement sparkline macro**

`web/templates/partials/sparkline.svg`:
```html
{# Jinja2 macro: render inline SVG sparkline from a list of values #}
{% macro sparkline(values, width=80, height=20, color="#3B82F6") %}
{% if values and values|length > 1 %}
{% set max_val = values|map('abs')|max %}
{% set min_val = values|map('abs')|min %}
{% set range_val = max_val - min_val if max_val != min_val else 1 %}
{% set step = width / (values|length - 1) %}
<svg width="{{ width }}" height="{{ height }}" class="inline-block align-middle">
    <polyline
        fill="none"
        stroke="{{ color }}"
        stroke-width="1.5"
        points="{% for v in values %}{{ loop.index0 * step }},{{ height - ((v|abs - min_val) / range_val * (height - 4) + 2) }}{% if not loop.last %} {% endif %}{% endfor %}"
    />
</svg>
{% endif %}
{% endmacro %}
```

- [ ] **Step 2: Implement trends route**

`web/routes/trends.py`:
```python
from datetime import date
from calendar import monthrange
from collections import defaultdict

from flask import Blueprint, current_app, render_template, request

from spending.repository.aggregations import get_monthly_totals_range

bp = Blueprint("trends", __name__)


def _period_range(period: str, today: date) -> tuple[date, date]:
    if period == "quarterly":
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        return date(today.year, quarter_start_month, 1), today
    elif period == "ytd":
        return date(today.year, 1, 1), today
    elif period == "trailing12":
        start_year = today.year - 1
        start_month = today.month + 1
        if start_month > 12:
            start_month -= 12
            start_year += 1
        return date(start_year, start_month, 1), today
    elif period == "annual":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    else:
        return date(today.year, 1, 1), today


@bp.route("/trends")
def index():
    today = date.today()
    period = request.args.get("period", "ytd")
    start, end = _period_range(period, today)

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        monthly_data = get_monthly_totals_range(conn, start_date=start, end_date=end)

    # Group by category, build per-month values for sparklines
    by_category = defaultdict(lambda: {"total": 0, "months": defaultdict(float)})
    all_months = set()
    for row in monthly_data:
        key = (row["year"], row["month"])
        all_months.add(key)
        by_category[row["category"]]["total"] += float(row["total"])
        by_category[row["category"]]["months"][key] = float(row["total"])

    sorted_months = sorted(all_months)
    num_months = max(len(sorted_months), 1)

    trends = []
    for cat, data in sorted(by_category.items(), key=lambda x: x[1]["total"]):
        sparkline_values = [abs(data["months"].get(m, 0)) for m in sorted_months]
        trends.append({
            "category": cat,
            "total": data["total"],
            "monthly_avg": data["total"] / num_months,
            "sparkline_values": sparkline_values,
        })

    grand_total = sum(t["total"] for t in trends)

    template = "partials/trends_table.html" if request.headers.get("HX-Request") else "trends.html"
    return render_template(
        template,
        active_tab="trends",
        trends=trends,
        grand_total=grand_total,
        period=period,
    )
```

- [ ] **Step 3: Create trends templates**

`web/templates/trends.html`:
```html
{% extends "base.html" %}
{% block content %}
<div class="flex items-center gap-2 mb-6">
    {% for p, label in [("quarterly", "Quarterly"), ("ytd", "Year to Date"), ("trailing12", "Trailing 12 Mo"), ("annual", "Last Year")] %}
    <a href="/trends?period={{ p }}"
       hx-get="/trends?period={{ p }}" hx-target="#content" hx-push-url="true"
       class="px-3 py-1.5 text-sm rounded {{ 'bg-blue-500 text-white' if period == p else 'bg-gray-100 text-gray-700 hover:bg-gray-200' }}">
        {{ label }}
    </a>
    {% endfor %}
</div>
{% include "partials/trends_table.html" %}
{% endblock %}
```

`web/templates/partials/trends_table.html`:
```html
{% from "partials/sparkline.svg" import sparkline %}
<table class="w-full bg-white rounded-lg shadow-sm">
    <thead>
        <tr class="border-b text-left text-sm text-gray-500">
            <th class="px-4 py-3">Category</th>
            <th class="px-4 py-3 text-right">Total</th>
            <th class="px-4 py-3 text-right">Monthly Avg</th>
            <th class="px-4 py-3 text-center">Trend</th>
        </tr>
    </thead>
    <tbody>
        {% for row in trends %}
        <tr class="border-b">
            <td class="px-4 py-3 font-medium">{{ row.category }}</td>
            <td class="px-4 py-3 text-right">${{ "%.2f"|format(row.total|abs) }}</td>
            <td class="px-4 py-3 text-right text-sm text-gray-500">${{ "%.2f"|format(row.monthly_avg|abs) }}</td>
            <td class="px-4 py-3 text-center">{{ sparkline(row.sparkline_values) }}</td>
        </tr>
        {% endfor %}
    </tbody>
    <tfoot>
        <tr class="font-semibold">
            <td class="px-4 py-3">Total</td>
            <td class="px-4 py-3 text-right">${{ "%.2f"|format(grand_total|abs) }}</td>
            <td></td>
            <td></td>
        </tr>
    </tfoot>
</table>
```

- [ ] **Step 4: Verify trends tab renders**

Run: `uv run python web/app.py`
Navigate to `http://localhost:5002/trends`
Expected: Trends page renders with period selector and table (may be empty)

- [ ] **Step 5: Commit**

```bash
git add web/routes/trends.py web/templates/trends.html web/templates/partials/trends_table.html web/templates/partials/sparkline.svg
git commit -m "feat: trends tab with preset periods and sparklines"
```

---

### Task 18: Merchants Tab

**Files:**
- Modify: `web/routes/merchants.py`
- Modify: `web/templates/merchants.html`
- Create: `web/templates/partials/merchant_rows.html`
- Create: `web/templates/partials/merchant_edit.html`

- [ ] **Step 1: Add merchant stats query**

Add to `spending/repository/merchants.py`:
```python
def list_merchants_with_stats(conn: Connection) -> list[dict]:
    """List merchants with transaction count and last seen date."""
    from sqlalchemy import func, select, coalesce
    from spending.models import transactions, transaction_corrections, imports

    resolved = coalesce(
        transaction_corrections.c.merchant_name,
        transactions.c.normalized_merchant,
    )

    stmt = (
        select(
            merchant_cache.c.id,
            merchant_cache.c.merchant_name,
            merchant_cache.c.category,
            merchant_cache.c.source,
            func.count(transactions.c.id).label("txn_count"),
            func.max(transactions.c.date).label("last_seen"),
        )
        .outerjoin(
            transactions,
            resolved == merchant_cache.c.merchant_name,
        )
        .outerjoin(
            transaction_corrections,
            transactions.c.id == transaction_corrections.c.transaction_id,
        )
        .outerjoin(imports, transactions.c.import_id == imports.c.id)
        .where((imports.c.status == "confirmed") | (imports.c.id.is_(None)))
        .group_by(merchant_cache.c.id)
        .order_by(merchant_cache.c.merchant_name)
    )
    rows = conn.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]
```

- [ ] **Step 2: Implement merchants route**

`web/routes/merchants.py`:
```python
from flask import Blueprint, current_app, render_template, request

from spending.repository.categories import get_category_names
from spending.repository.merchants import list_merchants_with_stats, set_merchant_category

bp = Blueprint("merchants", __name__)


@bp.route("/merchants")
def index():
    search = request.args.get("search", "")
    filter_category = request.args.get("category", "")
    filter_source = request.args.get("source", "")

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        merchants = list_merchants_with_stats(conn)
        categories = get_category_names(conn)

    if search:
        merchants = [m for m in merchants if search.upper() in m["merchant_name"].upper()]
    if filter_category:
        merchants = [m for m in merchants if m["category"] == filter_category]
    if filter_source:
        merchants = [m for m in merchants if m["source"] == filter_source]

    template = "partials/merchant_rows.html" if request.headers.get("HX-Request") else "merchants.html"
    return render_template(
        template,
        active_tab="merchants",
        merchants=merchants,
        categories=categories,
        search=search,
        selected_category=filter_category,
        selected_source=filter_source,
    )


@bp.route("/merchants/<int:merchant_id>/edit", methods=["GET"])
def edit_form(merchant_id):
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        categories = get_category_names(conn)
    return render_template("partials/merchant_edit.html", merchant_id=merchant_id, categories=categories)


@bp.route("/merchants/<int:merchant_id>/category", methods=["POST"])
def update_category(merchant_id):
    category = request.form["category"]
    merchant_name = request.form["merchant_name"]

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        set_merchant_category(conn, merchant_name, category, source="manual")

    return "", 200, {"HX-Trigger": "refreshMerchants"}
```

- [ ] **Step 3: Create merchants templates**

`web/templates/merchants.html`:
```html
{% extends "base.html" %}
{% block content %}
<div class="mb-4 flex flex-wrap gap-2 items-end">
    <input type="search" name="search" value="{{ search }}" placeholder="Search merchants..."
           hx-get="/merchants" hx-target="#content" hx-include="[name]"
           hx-trigger="keyup changed delay:300ms"
           class="border rounded px-2 py-1.5 text-sm">
    <select name="category" hx-get="/merchants" hx-target="#content" hx-include="[name]"
            class="border rounded px-2 py-1.5 text-sm">
        <option value="">All Categories</option>
        {% for c in categories %}
        <option value="{{ c }}" {{ 'selected' if selected_category == c }}>{{ c }}</option>
        {% endfor %}
    </select>
    <select name="source" hx-get="/merchants" hx-target="#content" hx-include="[name]"
            class="border rounded px-2 py-1.5 text-sm">
        <option value="">All Sources</option>
        <option value="api" {{ 'selected' if selected_source == 'api' }}>API</option>
        <option value="manual" {{ 'selected' if selected_source == 'manual' }}>Manual</option>
    </select>
</div>
{% include "partials/merchant_rows.html" %}
{% endblock %}
```

`web/templates/partials/merchant_rows.html`:
```html
<table class="w-full bg-white rounded-lg shadow-sm">
    <thead>
        <tr class="border-b text-left text-sm text-gray-500">
            <th class="px-4 py-3">Merchant</th>
            <th class="px-4 py-3">Category</th>
            <th class="px-4 py-3">Source</th>
            <th class="px-4 py-3 text-right">Transactions</th>
            <th class="px-4 py-3">Last Seen</th>
        </tr>
    </thead>
    <tbody>
        {% for m in merchants %}
        <tr class="border-b hover:bg-gray-50">
            <td class="px-4 py-3 font-medium text-sm">{{ m.merchant_name }}</td>
            <td class="px-4 py-3 text-sm cursor-pointer text-blue-600 hover:underline"
                hx-get="/merchants/{{ m.id }}/edit"
                hx-target="closest tr"
                hx-swap="afterend">
                {{ m.category }}
            </td>
            <td class="px-4 py-3 text-sm text-gray-500">{{ m.source }}</td>
            <td class="px-4 py-3 text-right text-sm">{{ m.txn_count }}</td>
            <td class="px-4 py-3 text-sm text-gray-500">{{ m.last_seen or '—' }}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
```

`web/templates/partials/merchant_edit.html`:
```html
<tr class="bg-blue-50 border-b">
    <td colspan="5" class="px-4 py-3">
        <form hx-post="/merchants/{{ merchant_id }}/category" hx-swap="none"
              class="flex items-center gap-3">
            <input type="hidden" name="merchant_name" value="">
            <select name="category" class="border rounded px-2 py-1.5 text-sm">
                {% for c in categories %}
                <option value="{{ c }}">{{ c }}</option>
                {% endfor %}
            </select>
            <button type="submit" class="bg-blue-500 text-white px-3 py-1 rounded text-sm">Save</button>
            <button type="button" class="text-gray-500 text-sm"
                    onclick="this.closest('tr').remove()">Cancel</button>
        </form>
    </td>
</tr>
```

- [ ] **Step 4: Verify merchants tab renders**

Run: `uv run python web/app.py`
Navigate to `http://localhost:5002/merchants`
Expected: Merchants page renders with filters and table

- [ ] **Step 5: Commit**

```bash
git add spending/repository/merchants.py web/routes/merchants.py web/templates/merchants.html web/templates/partials/merchant_rows.html web/templates/partials/merchant_edit.html
git commit -m "feat: merchants tab with inline category editing and filters"
```

---

### Task 19: Import Tab (Web)

**Files:**
- Modify: `web/routes/imports.py`
- Modify: `web/templates/import.html`
- Create: `web/templates/partials/import_batch.html`
- Modify: `web/static/app.js`

- [ ] **Step 1: Implement import routes**

`web/routes/imports.py`:
```python
import tempfile
from pathlib import Path

from flask import Blueprint, current_app, render_template, request

from spending.classifier import classify_merchants
from spending.importer import run_import
from spending.repository.accounts import get_account_by_id, list_accounts
from spending.repository.categories import get_category_names
from spending.repository.imports import confirm_import, get_staging_imports, reject_import
from spending.repository.merchants import get_uncached_merchants, set_merchant_category
from spending.repository.transactions import get_transactions

bp = Blueprint("imports", __name__)


@bp.route("/import")
def index():
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        staging = get_staging_imports(conn)
        accounts = list_accounts(conn)

    return render_template(
        "import.html",
        active_tab="import",
        staging=staging,
        accounts=accounts,
    )


@bp.route("/import/upload", methods=["POST"])
def upload():
    files = request.files.getlist("files")
    account_id = request.form.get("account_id", type=int)

    if not files or not account_id:
        return "<p class='text-red-500'>Please select files and an account.</p>", 400

    engine = current_app.config["engine"]
    results = []

    with engine.connect() as conn:
        all_new_merchants = set()

        for f in files:
            if not f.filename:
                continue
            # Save to temp file
            suffix = Path(f.filename).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                f.save(tmp.name)
                result = run_import(conn, tmp.name, account_id)
                result["filename"] = f.filename
                results.append(result)
                if not result.get("error"):
                    all_new_merchants.update(result.get("new_merchants", []))

        # Classify new merchants
        if all_new_merchants:
            uncached = get_uncached_merchants(conn, list(all_new_merchants))
            if uncached:
                category_names = get_category_names(conn)
                classifications = classify_merchants(uncached, category_names)
                for name, category in classifications.items():
                    set_merchant_category(conn, name, category, source="api")

        # Re-fetch staging imports
        staging = get_staging_imports(conn)
        accounts = list_accounts(conn)

    return render_template(
        "import.html",
        active_tab="import",
        staging=staging,
        accounts=accounts,
        results=results,
    )


@bp.route("/import/<int:import_id>/review")
def review(import_id):
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        txns = get_transactions(conn, import_id=import_id)
    return render_template("partials/import_batch.html", transactions=txns, import_id=import_id)


@bp.route("/import/<int:import_id>/confirm", methods=["POST"])
def confirm(import_id):
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        confirm_import(conn, import_id)
    return "", 200, {"HX-Trigger": "refreshImports"}


@bp.route("/import/<int:import_id>/reject", methods=["POST"])
def do_reject(import_id):
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        reject_import(conn, import_id)
    return "", 200, {"HX-Trigger": "refreshImports"}
```

- [ ] **Step 2: Create import template**

`web/templates/import.html`:
```html
{% extends "base.html" %}
{% block content %}
<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
    <!-- Upload zone -->
    <div>
        <h2 class="text-lg font-semibold mb-4">Import Statements</h2>
        <form hx-post="/import/upload" hx-target="#content" hx-encoding="multipart/form-data"
              class="space-y-4">
            <div id="dropzone"
                 class="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center
                        hover:border-blue-400 transition-colors cursor-pointer">
                <input type="file" name="files" multiple accept=".ofx,.qfx,.csv"
                       class="hidden" id="file-input">
                <p class="text-gray-500">Drag & drop files here, or click to select</p>
                <p class="text-sm text-gray-400 mt-1">Supports OFX, QFX, CSV</p>
                <div id="file-list" class="mt-3 text-sm text-gray-700"></div>
            </div>
            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">Account</label>
                <select name="account_id" required class="border rounded px-3 py-2 w-full text-sm">
                    <option value="">Select account...</option>
                    {% for a in accounts %}
                    <option value="{{ a.id }}">{{ a.name }}</option>
                    {% endfor %}
                </select>
            </div>
            <button type="submit" class="bg-blue-500 text-white px-4 py-2 rounded text-sm hover:bg-blue-600">
                Import
            </button>
        </form>

        {% if results %}
        <div class="mt-4 space-y-2">
            {% for r in results %}
            <div class="text-sm {{ 'text-red-500' if r.error else 'text-green-600' }}">
                {{ r.filename }}: {{ r.error if r.error else '%d new, %d skipped, %d flagged'|format(r.new_count, r.skipped_count, r.flagged_count) }}
            </div>
            {% endfor %}
        </div>
        {% endif %}
    </div>

    <!-- Staging review -->
    <div>
        <h2 class="text-lg font-semibold mb-4">Pending Imports</h2>
        {% if staging %}
        <div class="space-y-3">
            {% for imp in staging %}
            <div class="bg-white rounded-lg shadow-sm p-4">
                <div class="flex justify-between items-center mb-2">
                    <span class="font-medium text-sm">{{ imp.filename }}</span>
                    <span class="text-xs text-gray-500">{{ imp.imported_at }}</span>
                </div>
                <div id="batch-{{ imp.id }}"
                     hx-get="/import/{{ imp.id }}/review"
                     hx-trigger="load"
                     hx-swap="innerHTML">
                    <p class="text-sm text-gray-400">Loading...</p>
                </div>
                <div class="flex gap-2 mt-3">
                    <button hx-post="/import/{{ imp.id }}/confirm"
                            hx-target="#content"
                            class="bg-green-500 text-white px-3 py-1 rounded text-sm">
                        Confirm
                    </button>
                    <button hx-post="/import/{{ imp.id }}/reject"
                            hx-target="#content"
                            class="bg-red-500 text-white px-3 py-1 rounded text-sm">
                        Reject
                    </button>
                </div>
            </div>
            {% endfor %}
        </div>
        {% else %}
        <p class="text-gray-500 text-sm">No pending imports.</p>
        {% endif %}
    </div>
</div>
{% endblock %}
```

`web/templates/partials/import_batch.html`:
```html
<table class="w-full text-sm">
    <thead>
        <tr class="text-left text-gray-500">
            <th class="py-1">Date</th>
            <th class="py-1">Merchant</th>
            <th class="py-1">Category</th>
            <th class="py-1 text-right">Amount</th>
        </tr>
    </thead>
    <tbody>
        {% for txn in transactions %}
        <tr class="border-t border-gray-100">
            <td class="py-1">{{ txn.date }}</td>
            <td class="py-1">{{ txn.merchant }}</td>
            <td class="py-1 text-gray-500">{{ txn.category }}</td>
            <td class="py-1 text-right">${{ "%.2f"|format(txn.amount|abs) }}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
```

- [ ] **Step 3: Add drag-and-drop JS**

`web/static/app.js`:
```javascript
document.addEventListener('DOMContentLoaded', function() {
    // Drag and drop
    const dropzone = document.getElementById('dropzone');
    const fileInput = document.getElementById('file-input');
    const fileList = document.getElementById('file-list');

    if (dropzone && fileInput) {
        dropzone.addEventListener('click', () => fileInput.click());

        dropzone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropzone.classList.add('border-blue-400', 'bg-blue-50');
        });

        dropzone.addEventListener('dragleave', () => {
            dropzone.classList.remove('border-blue-400', 'bg-blue-50');
        });

        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropzone.classList.remove('border-blue-400', 'bg-blue-50');
            fileInput.files = e.dataTransfer.files;
            updateFileList();
        });

        fileInput.addEventListener('change', updateFileList);

        function updateFileList() {
            const files = fileInput.files;
            if (files.length === 0) {
                fileList.innerHTML = '';
                return;
            }
            const names = Array.from(files).map(f => f.name).join(', ');
            fileList.textContent = `Selected: ${names}`;
        }
    }

    // Select all checkbox
    document.addEventListener('change', function(e) {
        if (e.target.id === 'select-all') {
            const checkboxes = document.querySelectorAll('input[name="txn_ids"]');
            checkboxes.forEach(cb => cb.checked = e.target.checked);
        }
    });
});
```

- [ ] **Step 4: Verify import tab works**

Run: `uv run python web/app.py`
Navigate to `http://localhost:5002/import`
Expected: Upload zone renders with drag-and-drop area, account selector, and pending imports section

- [ ] **Step 5: Commit**

```bash
git add web/routes/imports.py web/templates/import.html web/templates/partials/import_batch.html web/static/app.js
git commit -m "feat: import tab with drag-and-drop upload and staging review"
```

---

### Task 20: Docker Setup

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `CLAUDE.md`

- [ ] **Step 1: Create Dockerfile**

`Dockerfile`:
```dockerfile
FROM python:3.12-slim AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev


FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv ./.venv

COPY spending/ ./spending/
COPY web/ ./web/
COPY configs/ ./configs/
COPY migrations/ ./migrations/
COPY spending.py ./
COPY alembic.ini ./

EXPOSE 5002

ENV SPENDING_DB=/app/data/spending.db

CMD [".venv/bin/flask", "--app", "web/app.py", "run", "--host", "0.0.0.0", "--port", "5002"]
```

- [ ] **Step 2: Create docker-compose.yml**

`docker-compose.yml`:
```yaml
services:
  spending:
    image: mrdefenestrator/spending:latest
    ports:
      - "5004:5002"
    volumes:
      - /volume1/docker/spending/data:/app/data
    environment:
      - SPENDING_DB=/app/data/spending.db
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5002/')"]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped
```

- [ ] **Step 3: Create CLAUDE.md**

`CLAUDE.md`:
```markdown
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
```

- [ ] **Step 4: Generate uv.lock**

Run: `uv lock`
Expected: `uv.lock` file generated

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml CLAUDE.md uv.lock
git commit -m "feat: Docker setup, CLAUDE.md, and lockfile"
```

---

## Task Dependencies

```
Task 1 (scaffold)
  └─▶ Task 2 (models) ──▶ Task 3 (alembic)
       ├─▶ Task 4 (categories)
       ├─▶ Task 5 (accounts + CLI)
       ├─▶ Task 6 (normalization)  ──┐
       ├─▶ Task 7 (OFX parser)   ───┤
       ├─▶ Task 8 (CSV parser)   ───┼─▶ Task 10 (import orchestration) ──┐
       ├─▶ Task 9 (dedup)        ───┘                                     │
       ├─▶ Task 11 (classifier)  ─────────────────────────────────────────┼─▶ Task 12 (CLI import)
       ├─▶ Task 13 (status + aggregations)                                │
       └─▶ Task 14 (web foundation)                                       │
            ├─▶ Task 15 (monthly tab)                                     │
            ├─▶ Task 16 (transactions tab)                                │
            ├─▶ Task 17 (trends tab)                                      │
            ├─▶ Task 18 (merchants tab)                                   │
            └─▶ Task 19 (import tab) ◀───────────────────────────────────┘
                 └─▶ Task 20 (docker)
```

**Parallelizable groups:**
- After Task 3: Tasks 4, 5, 6, 7, 8, 9, 11, 13 can run in parallel
- After Task 14: Tasks 15, 16, 17, 18 can run in parallel
