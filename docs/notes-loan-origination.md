# Loan origination data

**Status:** implemented · drafted 2026-07-22 · implemented 2026-07-25

## Goal

Capture where a loan started so we can derive its amortization, not just show
the current balance. Add three loan-only fields to `asset_entries`:

- `original_principal` — the original financed principal
- `term_months` — original amortization term (months)
- `origination_date` — date the loan was originated (schedule anchor)

`interest_rate` already exists. **We deliberately do NOT store monthly payment**
— for a fully-amortizing loan it is derivable from principal + rate + term, and
a stored copy would drift. We store `term` (canonical, clean integer) rather
than payment (payment as seen on a statement bundles escrow/PMI/HOA and would
corrupt the math).

Any two of {principal, term, payment} + rate determine the third; principal is
the field we're adding, so we need exactly one of {term, payment} → term.

This data applies only to loan rows stored as `asset_entries` debts. Legacy
`accounts` rows whose account type is `loan` are intentionally not enhanced;
those rows will eventually disappear when the unified tables are split.

Loan **Due** is a recurring day of month, identical to the account Due field.
The former `next_due_date` full date was an implementation mismatch and is
migrated to `statement_due_day_of_month` by retaining its day component.

## What the data unlocks

1. **Payoff progress / equity built** — `paid = original_principal − current
   balance`, and a % paid-off. Distinct from the existing Equity/LTV columns,
   which are the asset↔debt *pair* axis (property-secured), not payoff.
2. **Amortization + real payoff projection** — reconstruct the schedule:
   months remaining, projected payoff date, principal/interest split of the
   next payment, lifetime interest paid/remaining, "extra $X/mo" scenarios.
   Feeds the existing projections engine (real declining balance vs. crude
   extrapolation).
3. **Balance sanity-check** — principal + rate + origination_date → *expected*
   balance today; compare to entered current balance to surface stale/mis-typed
   data (the completeness/consistency checking the spreadsheet UI is built for).
4. **Interest vs. principal split** — feed interest portion to spending trends,
   principal portion to net-worth change, instead of one opaque outflow.

## Design decision (settled)

**Widen the Holdings grid (option B)** so loans show their inputs inline, rather
than hiding them in a drawer. Matches DESIGN.md spreadsheet-first: favor showing
fields, horizontal scroll is fine, a field is only inline-editable if it's a
visible column. Cost: bump `_NCOLS` and pad the other three groups with blank
`_E` slots.

## Implementation plan

### 1. Migration — `f4a5b6c7d8e9_add_loan_origination.py`
- Off current head `e3f4a5b6c7d8`.
- Three nullable columns, loan-only, no backfill. Same shape as the `unit`
  migration (`d2e3f4a5b6c7`).
  - `original_principal` `Numeric(14, 2)`
  - `term_months` `Integer`
  - `origination_date` `Date`
  - `statement_due_day_of_month` `Integer` (replaces `next_due_date`; existing
    dates retain their day component)

### 2. Plumbing (mechanical — follow existing patterns)
- **`core/models.py`** — add the three `Column(...)` to `asset_entries` next to
  `interest_rate`.
- **`core/types.py`** — add to the debt-only block of `AssetEntry`:
  `originalPrincipal: Decimal`, `termMonths: int`, `originationDate: str` (ISO),
  and `statement_due_day_of_month: int`.
- **`networth/repository.py`** — four symmetric touch-points:
  - add `origination_date` to `_DATE_COLS`
  - add the three col↔field pairs to the `optional_map` in
    `_row_to_asset_entry`, the `optional_map` in `_entry_dict_to_row`, and
    `_FIELD_TO_COL`
  - naming: `original_principal↔originalPrincipal`, `term_months↔termMonths`,
    `origination_date↔originationDate`
- No route/SQL changes — all driven off those maps.

### 3. Amortization helper (new: `fintrack/networth/amortization.py`)
Pure, DB-free, testable, shared by web + CLI:
- `scheduled_payment(principal, annual_rate, term_months) -> Decimal | None`
  (level P&I; r=0 → principal/term)
- `payoff_progress(original_principal, current_balance) -> Decimal | None`
  (fraction paid down; None if original missing)
- `expected_balance(original_principal, annual_rate, term_months,
  origination_date, as_of, due_day_of_month=None) -> Decimal | None`
- `projected_payoff_date(current_balance, annual_rate, scheduled_payment,
  as_of, due_day_of_month=None) -> date | None` (solve remaining n forward)

Payment dates use Due when set. A day that does not exist in a particular month
clamps to that month's final day; an unset Due means month-end. The first
payment is the first applicable due date strictly after origination.

### 4. Holdings Loans group — `web/routes/holdings.py`
- Bump `_NCOLS` from 11 to ~14–15.
- `_LOAN_COLS`: add `Original`, `Term`, `Originated` (editable inputs), plus
  `P&I` and `Paid` (derived, read-only). Keep Interest / Equity / LTV / Due /
  Linked / As Of.
- Pad `_CASH_COLS`, `_CREDIT_COLS`, `_ASSET_COLS` with extra `_E` blanks so every
  group still sums to the new `_NCOLS` (keeps grid + sticky actions column
  aligned; respect the sticky-row box-shadow border invariant).
- `_asset_col_fields` / `_asset_row`: wire the three new editable fields into
  `edit_raw`, and compute `Progress` (and optionally derived payment) via the
  amortization helper. Loan-group only.
- Remember the `.sheet-grid-filler` row when touching row selectors/e2e.

### 5. CLI parity — `fintrack/cli/` debts command group
- Add `--original-principal`, `--term-months`, `--origination-date` flags to
  debts add/edit.
- Show derived numbers (payment, payoff %, projected payoff) in debts display.
- Keep GUI (Holdings) and CLI (`accounts`/`assets`/`debts`) at parity.

### 6. Tests
- Unit: `amortization.py` math — zero-rate edge, mid-schedule expected balance,
  payoff date, progress None-guards.
- Repo round-trip for the three new fields.
- e2e: Loans group renders the new column(s); grid alignment intact.

## Deferred follow-up

Expected-vs-actual balance mismatch is available through the pure calculation
helper but is not yet flagged visually. Validate it against real loan examples
before assigning warning thresholds or colors.
