# Finances Tracker

## Summary

This is a design document for a new system.

The system is used for tracking, visualizing, and projecting accounts, income, expenses, assets, and debts.  The **top-level goals** of the system are to:

1. **Visualize liquid assets and get a total** — to understand ability to afford new things (spendable cash and equivalents, net of credit card debt).
2. **Visualize expected income and expenses over the next month and get a total** — to understand whether we are net saving or spending.
3. **Visualize assets and debts and get a total** — to understand net worth. Debts are tracked in a general way (e.g. mortgages, car loans); optional `assetRef` links a debt to an asset for paired net (5). Credit card and other unsecured debt are reflected in the liquid view instead.
4. **Generate an automatic monthly budget and annual budget** — to understand where money is being spent and the rate of saving. v1 provides a budget command (full month amounts, no proration; optional annual view and include/exclude by type). Category rollups and savings-rate reporting are planned extensions.

## Concepts

Terms such as *account*, *asset*, *liquid*, and *liability* follow standard financial industry definitions unless otherwise specified here.

### Tracking

- **Accounts:** Monetary balances in bank accounts, loans, wallets, apps. Each account has a required numeric `id` (incrementing integer) used for linking. Examples: savings, checking, credit card, loan balance, crypto wallet, physical cash, paypal, venmo, 401k, rewards balances, gift cards. Income and expense entries may specify `autoAccountRef` (account id) for where income is deposited or from which account an expense is paid (e.g. autopay). Credit card accounts may specify `paymentAccountRef` (account id) to record the checking/savings account from which the card's balance is automatically paid.
- **Income:** Expected payments (salary, refunds, remittances, bonuses). Optional `autoAccountRef` indicates the account that receives the payment.
- **Expenses:** Most are recurring (monthly, annual, quarterly, etc.) or one-time. Optional `autoAccountRef` indicates the account used to pay (e.g. autopay). Examples: food, gas, insurance, loan payments, utilities, subscriptions, car registration.
- **Assets:** Non-liquid items with an estimated value (e.g. homes, cars, crypto, 401k, HSA). Optional `quantity`; subtotal = value × quantity. Optional `source` for the valuation. Debts may reference an asset via `assetRef` for paired net worth (5).
- **Debts:** General debt (e.g. mortgages, car loans). Required `balance` (amount owed, per unit when `quantity` is used; subtotal = balance × quantity). Optional `quantity`; default 1. Optional `assetRef` links to an asset for (5); optional `interestRate` (decimal, e.g. 0.05 for 5%); optional `nextDueDate`, `asOfDate`.

**Transactions.** The system directly tracks only *scheduled* income and expenses (expected payments and outflows). Once those items become real, they are reflected in account balances, credit card balances, and the like. The system does not separately record actual transactions; current state is taken from account and balance data.

**Data.** One YAML file per snapshot (e.g. one household). The document must conform to `schema.yaml`. Required top-level keys: `accounts`, `income`, `expenses`, `assets`, `debts`. All data uses camelCase (e.g. `dayOfMonth`, `assetRef`, `autoAccountRef`).

### Visualizing

- Information on all items tracked is presented densely so the user can double-check. The following numbers are computed and used in tables and status:

 1. **(1)** Liquid asset/account total
 2. **(2)** Liquid minus credit card debts, plus credit card rewards (CC debt treated as already spent; statements paid in full)
 3. **(3)** Projected change to end of month (income minus expenses for remainder of month)
 4. **(4)** (2) + (3) — expected end-of-month total
 5. **(5)** Paired net: sum of (asset subtotal − debt balance) for each debt whose `assetRef` matches an asset
 6. **(6)** Net worth: sum of all non-liquid asset subtotals minus sum of all debts

- **Accounts table:** Each account (id, name, type, balance; for credit cards: limit, available, rewards, statement balance, due date, **Payment account** resolved from `paymentAccountRef`). Total = **(2)**. Shown by the **accounts** command and in **status**.
- **Budget table (income/expenses):** Kind, description, type, amount, **Subtotal** (expected for remainder of current month), **Monthly/Annual** (full budget amount), recurrence, when (day/date or continuous), **Auto account** (resolved from `autoAccountRef`). Shown by the **budget** command and in **status**.
- **Net-worth table (assets and debts):** Kind, institution, name, **Value** (per-unit value for assets, per-unit balance for debts), **Qty** (formatted; decimals preserved from data), subtotal (value × qty for assets, -(balance × qty) for debts), reference, **Interest rate** (debts only; shown as percentage). Total = **(6)**. Shown by the **net-worth** command and in **status**.
- **Status table (status command only):** Rows for Accounts **(2)**, Budget (prorated) **(3)**, Assets **(6)**, and a Total row (sum of the three).

Subtotal rules for the budget table (remainder of current month):

- **Monthly, not continuous:** Full amount if today’s date is before the entry’s `dayOfMonth` (the item has not yet occurred); otherwise 0.
- **Monthly, continuous:** Prorated by the proportion of the month remaining: `amount × (days_remaining / days_in_month)`.
- **Annual:** Full amount if the current month equals the entry’s `month` and today’s day is before the entry’s `dayOfYear` (or `dayOfMonth`); otherwise 0.
- **Quarterly:** Full amount if the current month is one of the entry’s quarter months (entry’s `month` and every 3 months) and today’s day is before the entry’s `dayOfMonth`; otherwise 0.
- **Semiannual:** Full amount if the current month is the entry’s `month` or 6 months later and today’s day is before the entry’s `dayOfMonth`; otherwise 0.
- **One-time:** Full amount if the entry’s `date` falls in the current month and is on or after today; otherwise 0.
- **Biweekly:** Expected for the remainder of the month is approximated as `2 × amount` (two pay periods); no day-based proration.

### CLI

- **status** *data_file* — Status table only (Accounts, Budget (prorated), Assets, Total).
- **accounts** *data_file* [*add* | *edit* \<id\> | *delete* \<id\>] — Accounts table, or add/edit/delete account. Subcommands: `add` (--name, --type, --balance or --limit/--available), `edit <id>`, `delete <id>` (--dry-run, --force).
- **budget** *data_file* — Income/expenses (budget) table with prorated subtotals and monthly/annual budget amounts. Optional `--annual` flag, filters by kind/type/recurrence.
- **income** *data_file* [*add* | *edit* \<index\> | *delete* \<index\>] — List income or add/edit/delete income entry. Subcommands: `add` (--description, --amount, --recurrence, …), `edit <index>`, `delete <index>` (--dry-run).
- **expenses** *data_file* [*add* | *edit* \<index\> | *delete* \<index\>] — List expenses or add/edit/delete expense entry. Same subcommand pattern as income.
- **assets** *data_file* [*add* | *edit* \<index\> | *delete* \<index\>] — Assets and debts table, or add/edit/delete asset. Subcommands: `add` (--name, --value, …), `edit <index>`, `delete <index>` (--dry-run).
- **debts** *data_file* [*add* | *edit* \<index\> | *delete* \<index\>] — List debts or add/edit/delete debt. Subcommands: `add` (--name, --balance, …), `edit <index>`, `delete <index>` (--dry-run).
- **budget** *data_file* [*--annual*] [*-i* TYPE] [*-x* TYPE] — Budget table (monthly or annual; optional include/exclude by type). Data file is the first argument for all commands.

### Mutations (add / edit / delete)

- **Data file is the source of truth.** CLI and web both write through the same layer (`finances.writer`). After every write, the document is validated against `schema.yaml`; on validation failure the write is not applied and an error is surfaced.
- **Reference integrity:** Deleting an account is forbidden if any income or expense has `autoAccountRef` pointing to that account, or if any credit card account has `paymentAccountRef` pointing to that account; the user must remove or change those references first. Deleting an asset is forbidden if any debt has `assetRef` pointing to that asset’s id; the user must remove or change those references first.
- **Web:** A global lock/unlock button in the header (left of the file picker) toggles edit mode for all tabs. Locked state is subdued; Editing state displays a prominent amber pill. Edit mode persists across tab navigation via the Flask session (`session[“edit_mode”]`). In Edit mode, each table supports inline single-cell editing (click cell to edit, save on blur/Enter), an always-visible empty row at the bottom for adding an entry, and per-row delete with inline “Delete? [Yes] [No]” confirmation. HTMX is used for swapping cell/row content and submitting updates; the server returns HTML fragments. Keyboard: Tab, Enter, Escape, arrows (documented in UI).

### Budget Calculation and Proration

- Budget calculation is: current balances plus the net of scheduled income minus scheduled expenses over the period; one-time items are included when their dates fall within the budget window.
- **Continuous** income or expenses (e.g. food, gas) have no fixed day; they are treated as spent or earned over the month. For the prorated subtotal (change to end of month), continuous monthly items are prorated by the proportion of the month remaining (e.g. on day 10 of a 30-day month, 2/3 of the monthly amount is counted).
- By default, the system calculates to the end of the present month, as this is the most common need, and the most reliable as there is the least uncertainty in these near term numbers.
- There will be a setting to project N months into the future (usually just one).  This involves totaling all the numbers that are known and expected over the time period.

### Web UI State

**URL-based state, no sessions.** The active YAML file and edit mode are encoded in the URL rather than Flask sessions. This enables multiple browser tabs to work on separate files simultaneously — each tab's state is fully determined by its own URL.

- **Active file:** path component `/f/<filename>/<section>` where `filename` is the file stem (no `.yaml` extension). Example: `/f/finances/accounts`, `/f/dechen/budget`.
- **Edit mode:** query parameter `?edit=1` on any route. Absence means locked/read-only.
- **File operations** (create, copy, rename, delete) navigate to the new file's URL via `HX-Redirect` (HTMX) or HTTP redirect (plain requests).
- **Sessions are not used.** No `SECRET_KEY` required. No cookie-based shared state.

### Web GUI Inline Editing

The web UI provides spreadsheet-style inline editing on Accounts, Budget, and Assets tables. A single global lock/unlock button in the header controls edit mode for all tabs; the edit state is encoded in the URL (`?edit=1`) so each browser tab can be independently locked or unlocked. In Edit mode, data cells are clickable and keyboard-navigable.

#### Cell Types

| Type | Visual | Behavior |
|------|--------|----------|
| **Display** | `cursor-pointer`, blue hover | Click opens edit mode for that cell (HTMX GET). |
| **Non-editable** | Grey background, lighter text | Computed/derived values (e.g. Subtotal, Monthly). Cannot be edited. Skipped by navigation. |
| **Editing** | Blue 2px inset box-shadow | Active edit state with input or select (or multiple inputs). Participates in keyboard navigation. |

In Locked mode, all cells are read-only with no hover/click behavior.

#### Entering Edit Mode

Click a display cell. HTMX fetches the edit form, swaps the entire `<tbody>`, and the input receives autofocus. Only one cell is in edit mode at a time — clicking a second cell saves the first (via focusout) and opens the second.

#### Keyboard Navigation

All editable cells follow the same keyboard rules, including cells with multiple inputs (e.g. month select + day input, or day input + continuous checkbox). Within a multi-input cell, the user clicks between sub-inputs.

| Key | Action | Focus moves to |
|-----|--------|---------------|
| **Tab** | Save | Next editable cell to the right |
| **Shift+Tab** | Save | Next editable cell to the left |
| **Enter** | Save | Same column, next row down |
| **Shift+Enter** | Save | Same column, next row up |
| **Escape** | Discard changes | Return to display (no movement) |
| **Click elsewhere** | Save via focusout | Clicked cell |

**Focus movement mechanism:** A `focus_direction` hidden form field is set by JS on keydown. The server renders the saved cell with a `data-focus-next` attribute. After the HTMX swap, JS reads the direction, finds the adjacent cell by row/column index, and auto-clicks it to enter edit mode.

**Edge behavior:** If there is no editable cell in the requested direction (e.g. Tab at the last column), focus is not moved and the user must click to re-enter edit mode.

#### Visual Feedback

- **Active cell**: 2px blue inset box-shadow while the input/select has focus.
- **Hover**: Display cells show light blue background on hover.
- **Non-editable**: Grey background and lighter text.

#### Add Row

Each table has a persistent add row at the bottom (visible only in Edit mode) with inputs for creating a new entry. Submitting triggers a full page refresh.

#### Delete

Each row in Edit mode has a delete icon (last column). Clicking reveals inline confirm/cancel buttons. Reference integrity is enforced server-side (cannot delete an account referenced by a budget entry, or an asset referenced by a debt).

## Out of scope / assumptions

- **Transaction model:** Only scheduled income/expenses are tracked as such; once real, they appear only via updated account/credit card balances (no separate “actual transaction” ledger).
- **Credit cards:** Carried balances are out of scope; we assume statements are paid in full each month.
- **Concepts not in scope for v1:** Multi-currency, multi-user or household sharing, investment performance/returns, and tax calculations.
- **Budget:** v1 provides the budget command (monthly or annual amounts, optional type filter). Category rollups and explicit savings-rate reporting are planned extensions.
