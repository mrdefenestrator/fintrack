# Balance Capture in Imports — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture and persist account balance data (ledger balance, available balance, beginning balance) from OFX and CSV imports into the `imports` table.

**Spec:** `docs/superpowers/specs/2026-05-29-balance-capture-design.md`

**Architecture:** Five nullable columns are added to `imports`. The OFX parser reads `statement.balance` / `statement.available_balance` (and their dates). The CSV parser reads optional `beginning_balance_column` / `ending_balance_column` config keys and scans rows for non-empty values. `ImportResult` gains matching optional fields. `create_import()` accepts and stores them. `run_import()` wires the two together.

**Tech Stack:** Python 3.12, SQLAlchemy Core, Alembic, ofxparse, pytest

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `spending/models.py` | Add 5 nullable balance columns to `imports` table |
| Create | `migrations/versions/<hash>_add_balance_columns_to_imports.py` | Alembic migration |
| Modify | `spending/types.py` | Add optional balance fields to `ImportResult` |
| Modify | `spending/repository/imports.py` | Update `create_import()` to accept and store balance fields |
| Modify | `tests/test_repository/test_imports.py` | Tests for updated `create_import()` |
| Modify | `spending/importer/ofx.py` | Extract balance fields from parsed OFX statement |
| Modify | `tests/test_importer/test_ofx.py` | Tests for balance extraction |
| Modify | `tests/test_importer/conftest.py` | Add OFX fixture with balance data |
| Modify | `spending/importer/csv_parser.py` | Read optional balance columns from config and data rows |
| Modify | `configs/institutions/venmo.yaml` | Add `beginning_balance_column` / `ending_balance_column` |
| Modify | `tests/test_importer/test_csv.py` | Tests for CSV balance extraction |
| Modify | `spending/importer/__init__.py` | Pass balance fields from `ImportResult` to `create_import()` |
| Modify | `tests/test_importer/test_pipeline.py` | Integration test: balance fields reach the DB |

---

### Task 1: Schema — add balance columns to `imports` and migrate

**Files:**
- Modify: `spending/models.py`
- Create: Alembic migration

- [ ] **Step 1: Add columns to `spending/models.py`**

In the `imports` table definition, add after the `status` column:

```python
Column("ledger_balance", Numeric(12, 2), nullable=True),
Column("ledger_balance_date", Date, nullable=True),
Column("available_balance", Numeric(12, 2), nullable=True),
Column("available_balance_date", Date, nullable=True),
Column("beginning_balance", Numeric(12, 2), nullable=True),
```

Add `Date` to the SQLAlchemy imports at the top of `models.py` if not already present.

- [ ] **Step 2: Generate the Alembic migration**

```bash
cd /Users/mikegauthiere/git/github.com/mrdefenestrator/spending
uv run alembic revision --autogenerate -m "add balance columns to imports"
```

Review the generated file in `migrations/versions/` and confirm it adds the five columns with `nullable=True`.

- [ ] **Step 3: Apply the migration**

```bash
uv run alembic upgrade head
```

- [ ] **Step 4: Run full test suite to confirm nothing broke**

```bash
uv run pytest --tb=short
```

Expected: all green (new columns are nullable, existing rows unaffected).

- [ ] **Step 5: Commit**

```bash
git add spending/models.py migrations/
git commit -m "feat: add balance columns to imports table"
```

---

### Task 2: `ImportResult` type + `create_import()` repository update

**Files:**
- Modify: `spending/types.py`
- Modify: `spending/repository/imports.py`
- Modify: `tests/test_repository/test_imports.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_repository/test_imports.py`:

```python
from decimal import Decimal
from datetime import date


def test_create_import_stores_ledger_balance(conn, sample_account_id):
    import_id = create_import(
        conn,
        account_id=sample_account_id,
        filename="test.ofx",
        file_hash="abc123",
        ledger_balance=Decimal("1234.56"),
        ledger_balance_date=date(2026, 1, 31),
    )
    row = conn.execute(
        select(imports).where(imports.c.id == import_id)
    ).fetchone()
    assert row.ledger_balance == Decimal("1234.56")
    assert row.ledger_balance_date == date(2026, 1, 31)


def test_create_import_stores_available_balance(conn, sample_account_id):
    import_id = create_import(
        conn,
        account_id=sample_account_id,
        filename="test.ofx",
        file_hash="def456",
        available_balance=Decimal("8765.44"),
        available_balance_date=date(2026, 1, 31),
    )
    row = conn.execute(
        select(imports).where(imports.c.id == import_id)
    ).fetchone()
    assert row.available_balance == Decimal("8765.44")


def test_create_import_stores_beginning_balance(conn, sample_account_id):
    import_id = create_import(
        conn,
        account_id=sample_account_id,
        filename="test.csv",
        file_hash="ghi789",
        beginning_balance=Decimal("0.00"),
        ledger_balance=Decimal("237.00"),
    )
    row = conn.execute(
        select(imports).where(imports.c.id == import_id)
    ).fetchone()
    assert row.beginning_balance == Decimal("0.00")
    assert row.ledger_balance == Decimal("237.00")


def test_create_import_balance_fields_default_to_null(conn, sample_account_id):
    import_id = create_import(
        conn,
        account_id=sample_account_id,
        filename="test.ofx",
        file_hash="jkl000",
    )
    row = conn.execute(
        select(imports).where(imports.c.id == import_id)
    ).fetchone()
    assert row.ledger_balance is None
    assert row.available_balance is None
    assert row.beginning_balance is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_repository/test_imports.py -v -k "balance"
```

Expected: `TypeError` — `create_import()` doesn't accept balance kwargs yet.

- [ ] **Step 3: Update `ImportResult` in `spending/types.py`**

Replace the existing `ImportResult` class:

```python
class ImportResult(TypedDict, total=False):
    transactions: list[ParsedTransaction]  # required
    account_name: str | None               # required
    ledger_balance: Decimal | None
    ledger_balance_date: date | None
    available_balance: Decimal | None
    available_balance_date: date | None
    beginning_balance: Decimal | None
```

Since `total=False` makes all keys optional, use a required-keys base class pattern to keep `transactions` and `account_name` required:

```python
class _ImportResultRequired(TypedDict):
    transactions: list[ParsedTransaction]
    account_name: str | None


class ImportResult(_ImportResultRequired, total=False):
    ledger_balance: Decimal | None
    ledger_balance_date: date | None
    available_balance: Decimal | None
    available_balance_date: date | None
    beginning_balance: Decimal | None
```

- [ ] **Step 4: Update `create_import()` in `spending/repository/imports.py`**

```python
def create_import(
    conn: Connection,
    *,
    account_id: int,
    filename: str,
    file_hash: str,
    ledger_balance: Decimal | None = None,
    ledger_balance_date: date | None = None,
    available_balance: Decimal | None = None,
    available_balance_date: date | None = None,
    beginning_balance: Decimal | None = None,
) -> int:
    result = conn.execute(
        insert(imports).values(
            account_id=account_id,
            filename=filename,
            file_hash=file_hash,
            ledger_balance=ledger_balance,
            ledger_balance_date=ledger_balance_date,
            available_balance=available_balance,
            available_balance_date=available_balance_date,
            beginning_balance=beginning_balance,
        )
    )
    conn.commit()
    return result.inserted_primary_key[0]
```

Add `from decimal import Decimal` and `from datetime import date` to imports if not present.

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_repository/test_imports.py -v
```

Expected: all pass including the 4 new balance tests.

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest --tb=short
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add spending/types.py spending/repository/imports.py tests/test_repository/test_imports.py
git commit -m "feat: add balance fields to ImportResult and create_import()"
```

---

### Task 3: OFX parser — extract balance fields

**Files:**
- Modify: `spending/importer/ofx.py`
- Modify: `tests/test_importer/conftest.py`
- Modify: `tests/test_importer/test_ofx.py`

- [ ] **Step 1: Add OFX fixture with balance data to `tests/test_importer/conftest.py`**

Add after the existing fixtures:

```python
@pytest.fixture
def sample_ofx_with_balances(tmp_path):
    content = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<BANKMSGSRSV1>
<STMTTRNRS>
<STMTRS>
<CURDEF>USD</CURDEF>
<BANKACCTFROM>
<BANKID>021000021</BANKID>
<ACCTID>9876543210</ACCTID>
<ACCTTYPE>CHECKING</ACCTTYPE>
</BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>20260101</DTSTART>
<DTEND>20260131</DTEND>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<DTPOSTED>20260115120000</DTPOSTED>
<TRNAMT>-42.50</TRNAMT>
<FITID>20260115001</FITID>
<NAME>WHOLE FOODS</NAME>
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL>
<BALAMT>1234.56</BALAMT>
<DTASOF>20260131120000</DTASOF>
</LEDGERBAL>
<AVAILBAL>
<BALAMT>1184.56</BALAMT>
<DTASOF>20260131120000</DTASOF>
</AVAILBAL>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>"""
    path = tmp_path / "test_balances.ofx"
    path.write_text(content)
    return path


@pytest.fixture
def sample_ofx_no_balances(tmp_path):
    content = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<BANKMSGSRSV1>
<STMTTRNRS>
<STMTRS>
<CURDEF>USD</CURDEF>
<BANKACCTFROM>
<BANKID>021000021</BANKID>
<ACCTID>1111</ACCTID>
<ACCTTYPE>CHECKING</ACCTTYPE>
</BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>20260101</DTSTART>
<DTEND>20260131</DTEND>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<DTPOSTED>20260115120000</DTPOSTED>
<TRNAMT>-10.00</TRNAMT>
<FITID>001</FITID>
<NAME>COFFEE</NAME>
</STMTTRN>
</BANKTRANLIST>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>"""
    path = tmp_path / "test_no_balances.ofx"
    path.write_text(content)
    return path
```

- [ ] **Step 2: Write failing tests in `tests/test_importer/test_ofx.py`**

```python
from decimal import Decimal
from datetime import date


def test_parse_ofx_ledger_balance(sample_ofx_with_balances):
    result = parse_ofx(sample_ofx_with_balances)
    assert result.get("ledger_balance") == Decimal("1234.56")


def test_parse_ofx_ledger_balance_date(sample_ofx_with_balances):
    result = parse_ofx(sample_ofx_with_balances)
    assert result.get("ledger_balance_date") == date(2026, 1, 31)


def test_parse_ofx_available_balance(sample_ofx_with_balances):
    result = parse_ofx(sample_ofx_with_balances)
    assert result.get("available_balance") == Decimal("1184.56")


def test_parse_ofx_available_balance_date(sample_ofx_with_balances):
    result = parse_ofx(sample_ofx_with_balances)
    assert result.get("available_balance_date") == date(2026, 1, 31)


def test_parse_ofx_no_balances_returns_none(sample_ofx_no_balances):
    result = parse_ofx(sample_ofx_no_balances)
    assert result.get("ledger_balance") is None
    assert result.get("available_balance") is None
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_importer/test_ofx.py -v -k "balance"
```

Expected: `AssertionError` — `parse_ofx()` doesn't return balance fields yet.

- [ ] **Step 4: Update `parse_ofx()` in `spending/importer/ofx.py`**

After building the `transactions` list and before the `return`, extract balance data:

```python
def parse_ofx(file_path: str | Path) -> ImportResult:
    with open(file_path, "rb") as f:
        ofx = OfxParser.parse(f)

    transactions: list[ParsedTransaction] = []
    ledger_balance = None
    ledger_balance_date = None
    available_balance = None
    available_balance_date = None

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
        stmt = account.statement
        if getattr(stmt, "balance", None) is not None:
            ledger_balance = Decimal(str(stmt.balance))
        if getattr(stmt, "balance_date", None) is not None:
            bal_date = stmt.balance_date
            ledger_balance_date = bal_date.date() if hasattr(bal_date, "date") else bal_date
        if getattr(stmt, "available_balance", None) is not None:
            available_balance = Decimal(str(stmt.available_balance))
        if getattr(stmt, "available_balance_date", None) is not None:
            avail_date = stmt.available_balance_date
            available_balance_date = avail_date.date() if hasattr(avail_date, "date") else avail_date

    return ImportResult(
        transactions=transactions,
        account_name=None,
        ledger_balance=ledger_balance,
        ledger_balance_date=ledger_balance_date,
        available_balance=available_balance,
        available_balance_date=available_balance_date,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_importer/test_ofx.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest --tb=short
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add spending/importer/ofx.py tests/test_importer/conftest.py tests/test_importer/test_ofx.py
git commit -m "feat: extract ledger and available balance from OFX imports"
```

---

### Task 4: CSV parser — extract beginning and ending balance

**Files:**
- Modify: `spending/importer/csv_parser.py`
- Modify: `configs/institutions/venmo.yaml`
- Modify: `tests/test_importer/test_csv.py`

- [ ] **Step 1: Write failing tests in `tests/test_importer/test_csv.py`**

Add a Venmo-style fixture and balance tests. Check the existing conftest for the fixture pattern and add:

```python
from decimal import Decimal


def test_parse_csv_venmo_beginning_balance(tmp_path, venmo_config_path):
    csv = tmp_path / "venmo.csv"
    csv.write_text(
        "Account Statement,,\n"
        "Account Activity,,\n"
        ",ID,Datetime,Type,Status,Note,From,To,Amount (total),Amount (tip),"
        "Amount (tax),Amount (fee),Tax Rate,Tax Exempt,Funding Source,"
        "Destination,Beginning Balance,Ending Balance,Statement Period Venmo Fees,"
        "Terminal Location,Year to Date Venmo Fees,Disclaimer\n"
        ",,,,,,,,,,,,,,,$50.00,,,,,,\n"  # beginning balance row
        ",1,2026-01-10T10:00:00,Payment,Complete,Lunch,Alice,Bob,+ $25.00,,0,,0,,,Venmo balance,,,,Venmo,,\n"
        ",,,,,,,,,,,,,,,,,$75.00,,,,,\n"  # ending balance row
    )
    result = parse_csv(csv, venmo_config_path)
    assert result.get("beginning_balance") == Decimal("50.00")


def test_parse_csv_venmo_ending_balance(tmp_path, venmo_config_path):
    csv = tmp_path / "venmo.csv"
    csv.write_text(
        "Account Statement,,\n"
        "Account Activity,,\n"
        ",ID,Datetime,Type,Status,Note,From,To,Amount (total),Amount (tip),"
        "Amount (tax),Amount (fee),Tax Rate,Tax Exempt,Funding Source,"
        "Destination,Beginning Balance,Ending Balance,Statement Period Venmo Fees,"
        "Terminal Location,Year to Date Venmo Fees,Disclaimer\n"
        ",,,,,,,,,,,,,,,$50.00,,,,,,\n"
        ",1,2026-01-10T10:00:00,Payment,Complete,Lunch,Alice,Bob,+ $25.00,,0,,0,,,Venmo balance,,,,Venmo,,\n"
        ",,,,,,,,,,,,,,,,,$75.00,,,,,\n"
    )
    result = parse_csv(csv, venmo_config_path)
    assert result.get("ledger_balance") == Decimal("75.00")


def test_parse_csv_no_balance_columns_returns_none(tmp_path, chase_config_path):
    # institution config with no balance columns defined
    result = parse_csv(some_chase_csv, chase_config_path)
    assert result.get("beginning_balance") is None
    assert result.get("ledger_balance") is None
```

> **Note:** Adapt the fixture names (`venmo_config_path`, `chase_config_path`) to match what exists in `tests/test_importer/conftest.py`. Add fixtures for the Venmo config path pointing to `configs/institutions/venmo.yaml` if not already present.

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_importer/test_csv.py -v -k "balance"
```

Expected: `AssertionError` — `parse_csv()` doesn't return balance fields yet.

- [ ] **Step 3: Update `parse_csv()` in `spending/importer/csv_parser.py`**

After loading config, read the optional column names:

```python
beg_bal_col = config.get("beginning_balance_column")
end_bal_col = config.get("ending_balance_column")
```

Inside the row loop, after the existing transaction-building logic, scan for balance values:

```python
beginning_balance = None
ledger_balance = None

# (inside the with open(...) block, after DictReader loop)
```

Restructure the row loop to a two-pass approach: first pass builds transactions (as now); second pass (or same pass with guards) picks up balance rows. Since balance rows have an empty `date_col`, they are already skipped by the `if not raw_date: continue` guard. Scan separately:

```python
with open(file_path, newline="") as f:
    for _ in range(header_row):
        f.readline()
    reader = csv.DictReader(f)
    all_rows = list(reader)

for row in all_rows:
    # existing transaction parsing (with continue guards as before)
    ...

if beg_bal_col:
    for row in all_rows:
        raw = row.get(beg_bal_col, "").strip()
        if raw:
            try:
                beginning_balance = _parse_signed_dollar(raw)
            except Exception:
                pass
            break

if end_bal_col:
    for row in all_rows:
        raw = row.get(end_bal_col, "").strip()
        if raw:
            try:
                ledger_balance = _parse_signed_dollar(raw)
            except Exception:
                pass
            break
```

Return the updated `ImportResult`:

```python
return ImportResult(
    transactions=transactions,
    account_name=config.get("account_name"),
    beginning_balance=beginning_balance,
    ledger_balance=ledger_balance,
)
```

- [ ] **Step 4: Update `configs/institutions/venmo.yaml`**

Add to the end of the file:

```yaml
beginning_balance_column: "Beginning Balance"
ending_balance_column: "Ending Balance"
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_importer/test_csv.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest --tb=short
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add spending/importer/csv_parser.py configs/institutions/venmo.yaml tests/test_importer/test_csv.py
git commit -m "feat: extract beginning and ending balance from CSV imports"
```

---

### Task 5: Wire balance fields through `run_import()`

**Files:**
- Modify: `spending/importer/__init__.py`
- Modify: `tests/test_importer/test_pipeline.py` (or equivalent integration test file)

- [ ] **Step 1: Write a failing integration test**

Add to `tests/test_importer/test_pipeline.py` (create if it doesn't exist):

```python
from decimal import Decimal
from sqlalchemy import select
from spending.models import imports
from spending.importer import run_import


def test_run_import_ofx_persists_ledger_balance(conn, sample_account_id, sample_ofx_with_balances):
    result = run_import(conn, sample_ofx_with_balances, account_id=sample_account_id)
    import_id = result["import_id"]
    row = conn.execute(select(imports).where(imports.c.id == import_id)).fetchone()
    assert row.ledger_balance == Decimal("1234.56")
    assert row.available_balance == Decimal("1184.56")


def test_run_import_venmo_csv_persists_balances(conn, sample_account_id, tmp_path):
    csv = tmp_path / "VenmoStatement_Test.csv"
    # minimal valid Venmo CSV with balance rows
    csv.write_text(
        "Account Statement,,\n"
        "Account Activity,,\n"
        ",ID,Datetime,Type,Status,Note,From,To,Amount (total),Amount (tip),"
        "Amount (tax),Amount (fee),Tax Rate,Tax Exempt,Funding Source,"
        "Destination,Beginning Balance,Ending Balance,Statement Period Venmo Fees,"
        "Terminal Location,Year to Date Venmo Fees,Disclaimer\n"
        ",,,,,,,,,,,,,,,$10.00,,,,,,\n"
        ",1,2026-01-10T10:00:00,Payment,Complete,Lunch,Alice,Bob,+ $25.00,,0,,0,,,Venmo balance,,,,Venmo,,\n"
        ",,,,,,,,,,,,,,,,,$35.00,,,,,\n"
    )
    result = run_import(conn, csv, account_id=sample_account_id)
    import_id = result["import_id"]
    row = conn.execute(select(imports).where(imports.c.id == import_id)).fetchone()
    assert row.beginning_balance == Decimal("10.00")
    assert row.ledger_balance == Decimal("35.00")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_importer/test_pipeline.py -v -k "balance"
```

Expected: `AssertionError` — balance fields in DB are still `None`.

- [ ] **Step 3: Update `run_import()` in `spending/importer/__init__.py`**

In `run_import()`, after `result = parse_ofx(file_path)` / `result = parse_csv(...)`, pass balance fields to `create_import()`:

```python
import_id = create_import(
    conn,
    account_id=account_id,
    filename=file_path.name,
    file_hash=file_hash,
    ledger_balance=result.get("ledger_balance"),
    ledger_balance_date=result.get("ledger_balance_date"),
    available_balance=result.get("available_balance"),
    available_balance_date=result.get("available_balance_date"),
    beginning_balance=result.get("beginning_balance"),
)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_importer/test_pipeline.py -v
```

Expected: all pass.

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest --tb=short
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add spending/importer/__init__.py tests/test_importer/test_pipeline.py
git commit -m "feat: wire balance fields from parsers through run_import to DB"
```

---

## Final verification

```bash
uv run pytest --tb=short
```

All tests green. The `imports` table now stores ledger balance, available balance, and beginning balance for every import that provides them.
