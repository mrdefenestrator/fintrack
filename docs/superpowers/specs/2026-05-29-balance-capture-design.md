# Balance Capture in Imports — Design Spec

**Date:** 2026-05-29
**Status:** Approved

## Problem

Statement imports capture transactions but discard the account balance data that is also present in the source files. OFX/QFX files include a ledger balance and an available balance (each with an as-of date). Venmo CSV exports include a beginning balance (in the first data row) and an ending balance (in the last data row). Storing this data alongside each import enables balance reconciliation, auto-updating account balances in downstream systems, and auditing over time.

## Approach

Extend the OFX and CSV parsers to extract balance data and propagate it through `ImportResult` to a new set of nullable columns on the `imports` table. No changes to the transaction pipeline, dedup logic, or staging/confirm flow.

## Data Model

Five nullable columns added to `imports`:

| Column | Type | Source |
|--------|------|--------|
| `ledger_balance` | Numeric(12,2), nullable | OFX `LEDGERBAL`; CSV ending balance |
| `ledger_balance_date` | Date, nullable | OFX `LEDGERBAL` as-of date; null for CSV |
| `available_balance` | Numeric(12,2), nullable | OFX `AVAILBAL` only |
| `available_balance_date` | Date, nullable | OFX `AVAILBAL` as-of date; null for CSV |
| `beginning_balance` | Numeric(12,2), nullable | CSV only (first data row) |

CSV ending balance maps to `ledger_balance` — it represents the authoritative account balance at the end of the statement period. `ledger_balance_date` is left null for CSV imports since the ending balance row carries no explicit date.

## Parser Changes

### OFX (`spending/importer/ofx.py`)

`parse_ofx()` reads from the already-parsed ofxparse object:

- `statement.balance` → `ledger_balance`
- `statement.balance_date` → `ledger_balance_date`
- `statement.available_balance` → `available_balance`
- `statement.available_balance_date` → `available_balance_date`

All four are `None` when the OFX file omits the corresponding tag (both are optional per the spec).

### CSV (`spending/importer/csv_parser.py`)

Two optional keys added to institution config:

```yaml
beginning_balance_column: "Beginning Balance"
ending_balance_column: "Ending Balance"
```

When present, the parser scans all data rows and takes:
- `beginning_balance`: value from the first row where the beginning balance column is non-empty
- `ledger_balance`: value from the first row where the ending balance column is non-empty

Both use the existing `_parse_signed_dollar` helper (Venmo balance cells use the same `+ $0.00` / `- $0.00` format as amount cells).

### Venmo config (`configs/institutions/venmo.yaml`)

Add:

```yaml
beginning_balance_column: "Beginning Balance"
ending_balance_column: "Ending Balance"
```

## Type Changes (`spending/types.py`)

`ImportResult` gains five optional fields mirroring the new columns:

```python
class ImportResult(TypedDict, total=False):
    # existing required keys promoted to a base class or kept with defaults
    ...
    ledger_balance: Decimal | None
    ledger_balance_date: date | None
    available_balance: Decimal | None
    available_balance_date: date | None
    beginning_balance: Decimal | None
```

All default to `None` so existing callers (tests) require no changes.

## Repository Changes (`spending/repository/imports.py`)

`create_import()` gains five optional keyword arguments (all `None` by default) and passes them to the `INSERT`. Existing call sites in `run_import` and tests continue to work unchanged until explicitly updated.

## Wire-up (`spending/importer/__init__.py`)

`run_import()` reads balance fields from the `ImportResult` returned by each parser and passes them to `create_import()`.

## Out of Scope

- Displaying balance data in the web UI (deferred)
- Auto-updating `finances` account balances from import data (cross-project, future)
- CSV institutions other than Venmo (no configs exist; new configs can add balance columns as needed)
- Balance reconciliation logic (future)
