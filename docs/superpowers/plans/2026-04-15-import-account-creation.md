# Import: Inline Account Creation with OFX Auto-detect — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to create an account inline on the import page, with fields pre-filled from OFX/QFX file metadata when a file is selected.

**Architecture:** A new `extract_ofx_metadata()` function in `ofx.py` parses institution, account type, and suggested name from OFX files without changing the existing `parse_ofx()` interface. A `POST /import/detect-account` endpoint calls this on the uploaded file and returns an `account_panel` HTML partial. A `POST /accounts` endpoint creates the account and returns the updated panel with the new account pre-selected. The account panel (`#account-panel`) lives inside the import form; account creation is triggered by a `type="button"` button with HTMX attributes and `hx-include` (no nested `<form>` element, which is invalid HTML). JS sends the first selected file to `/import/detect-account` via `fetch` and replaces `#account-panel` using `outerHTML`, then calls `htmx.process(document.body)` so HTMX initialises the new buttons.

**Tech Stack:** Python 3.12, Flask, HTMX 1.x, Jinja2, SQLAlchemy Core, ofxparse, pytest

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `spending/types.py` | Add `AccountMeta` TypedDict |
| Modify | `spending/importer/ofx.py` | Add `extract_ofx_metadata()` |
| Modify | `tests/test_importer/conftest.py` | Add `sample_ofx_with_meta` fixture |
| Modify | `tests/test_importer/test_ofx.py` | Tests for `extract_ofx_metadata` |
| Create | `web/templates/partials/account_panel.html` | Account selection + inline create form |
| Create | `web/routes/accounts.py` | `POST /accounts` blueprint |
| Modify | `web/routes/__init__.py` | Register accounts blueprint |
| Create | `tests/test_web/__init__.py` | Empty package marker |
| Create | `tests/test_web/conftest.py` | Flask test client + OFX fixture |
| Create | `tests/test_web/test_accounts.py` | Tests for `POST /accounts` |
| Modify | `web/routes/imports.py` | Add `POST /import/detect-account` |
| Create | `tests/test_web/test_detect_account.py` | Tests for detect endpoint |
| Modify | `web/templates/import.html` | Replace account `<div>` with `{% with %}` include |
| Modify | `web/static/app.js` | Send file to detect-account on change/drop |

---

### Task 1: AccountMeta TypedDict + extract_ofx_metadata()

**Files:**
- Modify: `spending/types.py`
- Modify: `spending/importer/ofx.py`
- Modify: `tests/test_importer/conftest.py`
- Modify: `tests/test_importer/test_ofx.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_importer/conftest.py` (after the existing `sample_csv` fixture):

```python
@pytest.fixture
def sample_ofx_with_meta(tmp_path):
    content = """<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX>
  <SIGNONMSGSRSV1>
    <SONRS>
      <STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>
      <DTSERVER>20240115120000</DTSERVER>
      <LANGUAGE>ENG</LANGUAGE>
      <FI><ORG>Chase</ORG><FID>10898</FID></FI>
    </SONRS>
  </SIGNONMSGSRSV1>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <STMTRS>
        <BANKACCTFROM>
          <ACCTID>1234567890</ACCTID>
          <ACCTTYPE>CHECKING</ACCTTYPE>
        </BANKACCTFROM>
        <BANKTRANLIST>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20240115120000</DTPOSTED>
            <TRNAMT>-42.50</TRNAMT>
            <FITID>20240115001</FITID>
            <NAME>WHOLE FOODS</NAME>
          </STMTTRN>
        </BANKTRANLIST>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>"""
    path = tmp_path / "test_meta.ofx"
    path.write_text(content)
    return path
```

Add to `tests/test_importer/test_ofx.py`:

```python
from spending.importer.ofx import extract_ofx_metadata


def test_extract_ofx_metadata_institution(sample_ofx_with_meta):
    meta = extract_ofx_metadata(sample_ofx_with_meta)
    assert meta is not None
    assert meta["institution"] == "Chase"


def test_extract_ofx_metadata_account_type(sample_ofx_with_meta):
    meta = extract_ofx_metadata(sample_ofx_with_meta)
    assert meta is not None
    assert meta["account_type"] == "checking"


def test_extract_ofx_metadata_suggested_name(sample_ofx_with_meta):
    meta = extract_ofx_metadata(sample_ofx_with_meta)
    assert meta is not None
    assert "7890" in meta["suggested_name"]
    assert "Chase" in meta["suggested_name"]


def test_extract_ofx_metadata_corrupt_file(tmp_path):
    bad = tmp_path / "bad.ofx"
    bad.write_text("not valid ofx content at all")
    meta = extract_ofx_metadata(bad)
    assert meta is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_importer/test_ofx.py -v -k "extract_ofx"
```

Expected: `ImportError` or `AttributeError` — `extract_ofx_metadata` does not exist yet.

- [ ] **Step 3: Add AccountMeta to types.py**

`spending/types.py` — add after the existing TypedDicts:

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


class AccountMeta(TypedDict):
    institution: str       # e.g. "Chase" (empty string if unavailable)
    account_type: str      # "checking" | "savings" | "credit" | "other"
    suggested_name: str    # e.g. "Chase Checking ...7890"
```

- [ ] **Step 4: Add extract_ofx_metadata() to ofx.py**

`spending/importer/ofx.py` — full file after changes:

```python
from decimal import Decimal
from pathlib import Path

from ofxparse import OfxParser

from spending.types import AccountMeta, ImportResult, ParsedTransaction

_ACCOUNT_TYPE_MAP = {
    "CHECKING": "checking",
    "SAVINGS": "savings",
    "MONEYMRKT": "savings",
    "CREDITLINE": "credit",
    "CREDITCARD": "credit",
}


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


def extract_ofx_metadata(file_path: str | Path) -> AccountMeta | None:
    """Parse OFX institution/account metadata without importing transactions.

    Returns None on any parse failure so callers degrade gracefully.
    """
    try:
        with open(file_path, "rb") as f:
            ofx = OfxParser.parse(f)

        account = ofx.account
        if not account:
            return None

        institution = ""
        if account.institution and account.institution.organization:
            institution = account.institution.organization

        raw_type = (account.account_type or "").upper()
        account_type = _ACCOUNT_TYPE_MAP.get(raw_type, "other")

        account_id = account.account_id or ""
        last4 = account_id[-4:] if len(account_id) >= 4 else account_id

        parts = []
        if institution:
            parts.append(institution)
        if account_type and account_type != "other":
            parts.append(account_type.capitalize())
        if last4:
            parts.append(f"...{last4}")
        suggested_name = " ".join(parts) if parts else "New Account"

        return AccountMeta(
            institution=institution,
            account_type=account_type,
            suggested_name=suggested_name,
        )
    except Exception:
        return None
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_importer/test_ofx.py -v
```

Expected: all tests pass including the 4 new ones.

- [ ] **Step 6: Run full test suite to check nothing broke**

```bash
uv run pytest --tb=short
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add spending/types.py spending/importer/ofx.py \
        tests/test_importer/conftest.py tests/test_importer/test_ofx.py
git commit -m "feat: add AccountMeta TypedDict and extract_ofx_metadata()"
```

---

### Task 2: account_panel.html partial template

**Files:**
- Create: `web/templates/partials/account_panel.html`

This template always renders a `<div id="account-panel">` (so `outerHTML` swaps work). It uses three template variables that callers pass explicitly: `accounts` (list of account dicts), `meta` (AccountMeta or None), `selected_account_id` (int or None), `show_create` (bool), `error` (str or None).

- [ ] **Step 1: Create the template**

`web/templates/partials/account_panel.html`:

```html
<div id="account-panel">
    <label class="block text-sm font-medium text-gray-700 mb-1">Account</label>

    {% if accounts %}
    <select name="account_id" required class="border rounded px-3 py-2 w-full text-sm">
        <option value="">Select account...</option>
        {% for a in accounts %}
        <option value="{{ a.id }}"
                {% if selected_account_id and a.id == selected_account_id %}selected{% endif %}>
            {{ a.name }}
        </option>
        {% endfor %}
    </select>
    {% endif %}

    {% if show_create or not accounts %}
    {# Expanded create form — shown when no accounts exist or explicitly requested #}
    <div class="mt-3 p-3 border rounded bg-gray-50 space-y-2">
        {% if not accounts %}
        <p class="text-sm text-amber-600 font-medium">No accounts yet — create one to continue.</p>
        {% else %}
        <p class="text-sm text-gray-600 font-medium">Create new account</p>
        {% endif %}

        {% if error %}
        <p class="text-sm text-red-500">{{ error }}</p>
        {% endif %}

        <input type="text" name="acct_name"
               placeholder="Account name (e.g. Chase Checking)"
               value="{{ meta.suggested_name if meta else '' }}"
               class="border rounded px-3 py-2 w-full text-sm">
        <input type="text" name="acct_institution"
               placeholder="Institution (e.g. Chase)"
               value="{{ meta.institution if meta else '' }}"
               class="border rounded px-3 py-2 w-full text-sm">
        <select name="acct_type" class="border rounded px-3 py-2 w-full text-sm">
            <option value="checking"
                    {% if meta and meta.account_type == 'checking' %}selected{% endif %}>
                Checking
            </option>
            <option value="savings"
                    {% if meta and meta.account_type == 'savings' %}selected{% endif %}>
                Savings
            </option>
            <option value="credit"
                    {% if meta and meta.account_type == 'credit' %}selected{% endif %}>
                Credit Card
            </option>
            <option value="other"
                    {% if not meta or meta.account_type == 'other' %}selected{% endif %}>
                Other
            </option>
        </select>
        <button type="button"
                hx-post="/accounts"
                hx-target="#account-panel"
                hx-swap="outerHTML"
                hx-include="[name='acct_name'],[name='acct_institution'],[name='acct_type']"
                class="bg-blue-500 text-white px-3 py-1 rounded text-sm hover:bg-blue-600">
            Create Account
        </button>
    </div>
    {% else %}
    {# Collapsed toggle — shown when accounts exist and show_create is False #}
    <details class="mt-1">
        <summary class="text-sm text-blue-600 cursor-pointer select-none">
            + Create new account
        </summary>
        <div class="mt-2 p-3 border rounded bg-gray-50 space-y-2">
            {% if error %}
            <p class="text-sm text-red-500">{{ error }}</p>
            {% endif %}

            <input type="text" name="acct_name"
                   placeholder="Account name (e.g. Chase Checking)"
                   value="{{ meta.suggested_name if meta else '' }}"
                   class="border rounded px-3 py-2 w-full text-sm">
            <input type="text" name="acct_institution"
                   placeholder="Institution (e.g. Chase)"
                   value="{{ meta.institution if meta else '' }}"
                   class="border rounded px-3 py-2 w-full text-sm">
            <select name="acct_type" class="border rounded px-3 py-2 w-full text-sm">
                <option value="checking"
                        {% if meta and meta.account_type == 'checking' %}selected{% endif %}>
                    Checking
                </option>
                <option value="savings"
                        {% if meta and meta.account_type == 'savings' %}selected{% endif %}>
                    Savings
                </option>
                <option value="credit"
                        {% if meta and meta.account_type == 'credit' %}selected{% endif %}>
                    Credit Card
                </option>
                <option value="other"
                        {% if not meta or meta.account_type == 'other' %}selected{% endif %}>
                    Other
                </option>
            </select>
            <button type="button"
                    hx-post="/accounts"
                    hx-target="#account-panel"
                    hx-swap="outerHTML"
                    hx-include="[name='acct_name'],[name='acct_institution'],[name='acct_type']"
                    class="bg-blue-500 text-white px-3 py-1 rounded text-sm hover:bg-blue-600">
                Create Account
            </button>
        </div>
    </details>
    {% endif %}
</div>
```

- [ ] **Step 2: Commit**

```bash
git add web/templates/partials/account_panel.html
git commit -m "feat: add account_panel partial template"
```

---

### Task 3: POST /accounts route

**Files:**
- Create: `web/routes/accounts.py`
- Modify: `web/routes/__init__.py`
- Create: `tests/test_web/__init__.py`
- Create: `tests/test_web/conftest.py`
- Create: `tests/test_web/test_accounts.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web/__init__.py` (empty):

```python
```

Create `tests/test_web/conftest.py`:

```python
import pytest

from web.app import create_app


@pytest.fixture
def app(tmp_path):
    db_path = tmp_path / "test.db"
    application = create_app(db_path=str(db_path))
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def conn(app):
    engine = app.config["engine"]
    with engine.connect() as connection:
        yield connection
```

Create `tests/test_web/test_accounts.py`:

```python
from spending.repository.accounts import add_account


def test_create_account_success(client):
    response = client.post("/accounts", data={
        "acct_name": "Chase Checking",
        "acct_institution": "Chase",
        "acct_type": "checking",
    })
    assert response.status_code == 200
    html = response.data.decode()
    assert "Chase Checking" in html
    assert "selected" in html


def test_create_account_duplicate_name_shows_error(client, conn):
    add_account(conn, name="Chase Checking", institution="Chase", account_type="checking")
    response = client.post("/accounts", data={
        "acct_name": "Chase Checking",
        "acct_institution": "Chase",
        "acct_type": "checking",
    })
    assert response.status_code == 200
    html = response.data.decode()
    assert "already exists" in html


def test_create_account_missing_name_shows_error(client):
    response = client.post("/accounts", data={
        "acct_name": "",
        "acct_institution": "Chase",
        "acct_type": "checking",
    })
    assert response.status_code == 200
    html = response.data.decode()
    assert "required" in html
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_web/test_accounts.py -v
```

Expected: `ImportError` or 404 — the blueprint and route don't exist yet.

- [ ] **Step 3: Create web/routes/accounts.py**

```python
from flask import Blueprint, current_app, render_template, request
from sqlalchemy.exc import IntegrityError

from spending.repository.accounts import add_account, list_accounts

bp = Blueprint("accounts", __name__)


@bp.route("/accounts", methods=["POST"])
def create():
    name = request.form.get("acct_name", "").strip()
    institution = request.form.get("acct_institution", "").strip()
    account_type = request.form.get("acct_type", "checking")

    engine = current_app.config["engine"]

    with engine.connect() as conn:
        accounts = list_accounts(conn)

        if not name or not institution:
            return render_template(
                "partials/account_panel.html",
                accounts=accounts,
                meta=None,
                selected_account_id=None,
                show_create=True,
                error="Name and institution are required.",
            )

        try:
            new_id = add_account(
                conn, name=name, institution=institution, account_type=account_type
            )
            accounts = list_accounts(conn)
        except IntegrityError:
            return render_template(
                "partials/account_panel.html",
                accounts=accounts,
                meta=None,
                selected_account_id=None,
                show_create=True,
                error=f'Account "{name}" already exists.',
            )

    return render_template(
        "partials/account_panel.html",
        accounts=accounts,
        meta=None,
        selected_account_id=new_id,
        show_create=False,
        error=None,
    )
```

- [ ] **Step 4: Register the blueprint in web/routes/__init__.py**

```python
from flask import Flask


def register_blueprints(app: Flask) -> None:
    from web.routes.accounts import bp as accounts_bp
    from web.routes.imports import bp as imports_bp
    from web.routes.merchants import bp as merchants_bp
    from web.routes.monthly import bp as monthly_bp
    from web.routes.transactions import bp as transactions_bp
    from web.routes.trends import bp as trends_bp

    app.register_blueprint(accounts_bp)
    app.register_blueprint(monthly_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(trends_bp)
    app.register_blueprint(merchants_bp)
    app.register_blueprint(imports_bp)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_web/test_accounts.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest --tb=short
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add web/routes/accounts.py web/routes/__init__.py \
        tests/test_web/__init__.py tests/test_web/conftest.py \
        tests/test_web/test_accounts.py
git commit -m "feat: add POST /accounts route for inline account creation"
```

---

### Task 4: POST /import/detect-account route

**Files:**
- Modify: `web/routes/imports.py`
- Create: `tests/test_web/test_detect_account.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web/test_detect_account.py`:

```python
import io
import pytest


@pytest.fixture
def ofx_with_meta_bytes(tmp_path):
    content = """<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX>
  <SIGNONMSGSRSV1>
    <SONRS>
      <STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>
      <DTSERVER>20240115120000</DTSERVER>
      <LANGUAGE>ENG</LANGUAGE>
      <FI><ORG>Chase</ORG><FID>10898</FID></FI>
    </SONRS>
  </SIGNONMSGSRSV1>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <STMTRS>
        <BANKACCTFROM>
          <ACCTID>1234567890</ACCTID>
          <ACCTTYPE>CHECKING</ACCTTYPE>
        </BANKACCTFROM>
        <BANKTRANLIST>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20240115120000</DTPOSTED>
            <TRNAMT>-42.50</TRNAMT>
            <FITID>20240115001</FITID>
            <NAME>WHOLE FOODS</NAME>
          </STMTTRN>
        </BANKTRANLIST>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>"""
    return content.encode()


def test_detect_account_ofx_prefills_institution(client, ofx_with_meta_bytes):
    response = client.post(
        "/import/detect-account",
        data={"files": (io.BytesIO(ofx_with_meta_bytes), "test.ofx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    html = response.data.decode()
    assert "Chase" in html


def test_detect_account_ofx_no_accounts_shows_expanded_form(client, ofx_with_meta_bytes):
    response = client.post(
        "/import/detect-account",
        data={"files": (io.BytesIO(ofx_with_meta_bytes), "test.ofx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    html = response.data.decode()
    assert "No accounts yet" in html


def test_detect_account_csv_returns_empty_form(client):
    csv_bytes = b"Transaction Date,Description,Amount\n01/15/2024,COFFEE,-5.00\n"
    response = client.post(
        "/import/detect-account",
        data={"files": (io.BytesIO(csv_bytes), "test.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    # form rendered without error
    assert b"account-panel" in response.data


def test_detect_account_corrupt_file_degrades_gracefully(client):
    response = client.post(
        "/import/detect-account",
        data={"files": (io.BytesIO(b"not valid ofx"), "test.ofx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert b"account-panel" in response.data
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_web/test_detect_account.py -v
```

Expected: 404 — the route doesn't exist yet.

- [ ] **Step 3: Add detect_account route to web/routes/imports.py**

Add one import to the existing import block in `web/routes/imports.py`:

```python
from spending.importer.ofx import extract_ofx_metadata
```

Add this route to `web/routes/imports.py` (after the `upload` route, before `review`):

```python
@bp.route("/import/detect-account", methods=["POST"])
def detect_account():
    file = request.files.get("files")
    meta = None

    if file and file.filename:
        suffix = Path(file.filename).suffix.lower()
        if suffix in (".ofx", ".qfx"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                file.save(tmp.name)
                meta = extract_ofx_metadata(tmp.name)

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        accounts = list_accounts(conn)

    return render_template(
        "partials/account_panel.html",
        accounts=accounts,
        meta=meta,
        selected_account_id=None,
        show_create=(len(accounts) == 0),
        error=None,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_web/test_detect_account.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest --tb=short
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add web/routes/imports.py tests/test_web/test_detect_account.py
git commit -m "feat: add POST /import/detect-account endpoint"
```

---

### Task 5: Wire up import.html and app.js

**Files:**
- Modify: `web/templates/import.html`
- Modify: `web/static/app.js`

No new tests — the backend is covered by Tasks 3 and 4.

- [ ] **Step 1: Update import.html**

Replace the account `<div>` block in `web/templates/import.html`. The full updated form section (replace everything inside the `<form>` tag):

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
            {% with meta=none, selected_account_id=none, show_create=(accounts|length == 0), error=none %}
            {% include "partials/account_panel.html" %}
            {% endwith %}
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

- [ ] **Step 2: Update app.js**

Replace the full content of `web/static/app.js`:

```javascript
function detectAccount(file) {
    const formData = new FormData();
    formData.append('files', file);
    fetch('/import/detect-account', { method: 'POST', body: formData })
        .then(r => r.text())
        .then(html => {
            const panel = document.getElementById('account-panel');
            if (panel) {
                panel.outerHTML = html;
                htmx.process(document.body);
            }
        });
}

function initDropzone() {
    const dropzone = document.getElementById('dropzone');
    const fileInput = document.getElementById('file-input');
    const fileList = document.getElementById('file-list');

    if (!dropzone || !fileInput) return;

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
        if (fileInput.files.length > 0) {
            detectAccount(fileInput.files[0]);
        }
    });

    fileInput.addEventListener('change', function () {
        updateFileList();
        if (fileInput.files.length > 0) {
            detectAccount(fileInput.files[0]);
        }
    });

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

document.addEventListener('DOMContentLoaded', function () {
    initDropzone();

    // Select all checkbox
    document.addEventListener('change', function (e) {
        if (e.target.id === 'select-all') {
            const checkboxes = document.querySelectorAll('input[name="txn_ids"]');
            checkboxes.forEach(cb => cb.checked = e.target.checked);
        }
    });
});

// Re-init after HTMX swaps content (DOMContentLoaded only fires once)
document.addEventListener('htmx:afterSettle', initDropzone);
```

- [ ] **Step 3: Manually verify the happy path**

Run the dev server:
```bash
mise run serve
```

Open http://localhost:5002/import and verify:
1. With an empty DB: the "No accounts yet" message and create form appear immediately
2. Selecting an OFX/QFX file: the institution and account name fields are pre-filled
3. Creating an account: the create form disappears, the new account is selected in the dropdown
4. Clicking Import proceeds normally

- [ ] **Step 4: Run full test suite one more time**

```bash
uv run pytest --tb=short
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add web/templates/import.html web/static/app.js
git commit -m "feat: wire up inline account creation and OFX auto-detect in import UI"
```
