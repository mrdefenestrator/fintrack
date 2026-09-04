# Holdings table — design guide & fixed spec

The Holdings sheet is the most feature-complete table in the app and the
reference the other finances sheets are meant to converge on. It is also the one
we most often break by accident when touching it, because a lot of its behaviour
lives in **non-obvious CSS invariants** (sticky-row borders, z-index ordering,
box-shadow "borders") rather than in the markup you're editing.

This document is the **spec of record** for that table: what every feature is,
how it's built, and — most importantly — the invariants that must survive any
change. Treat the "Invariants" section as a checklist before you open a PR that
touches Holdings. If a change here contradicts DESIGN.md, one of them is wrong;
reconcile them, don't leave both.

**Not everything here is locked.** §16 (Invariants) and §17 (Deliberate
tradeoffs) are the settled contract. §18 (Provisional / open questions) lists
current behaviours that are described so they aren't *lost*, but that are **not
yet ratified as spec** — don't enshrine or build hard dependencies on them
without checking intent first.

DESIGN.md's [Holdings sheet](../DESIGN.md) section is the short architectural
summary; this is the long-form, feature-by-feature version.

---

## 1. Where everything lives

| Concern | File |
| --- | --- |
| Page shell, `<colgroup>`, all Holdings-specific CSS | `web/templates/holdings.html` |
| Table body: group bands, rows, headers, add-rows, footer | `web/templates/partials/holdings_tbody.html` |
| Row actions cell (drag + delete) | `web/templates/partials/holdings_actions_cell.html` |
| Delete confirm cell | `web/templates/partials/holdings_delete_confirm.html` |
| Routes, column layouts, row assembly, edit gating | `web/routes/holdings.py` |
| Per-group column sorting | `web/static/js/holdings-sort.js` |
| Scroll shadows, sticky footer filler, grid canvas | `web/static/js/sheet-scroll.js` |
| Row drag-reorder | `web/static/js/row-reorder.js` |
| **Shared** sheet chrome (sticky header/footer, cell/row classes, scroll shadows, freeze-col) | `web/templates/base.html` (`<style>`), `web/templates/partials/sheet.html`, `web/templates/macros/table.html` |

Holdings deliberately shares its low-level chrome with the other sheets
(Accounts data now lives here, Budget, Transactions, Merchants). **CSS you change
in `base.html` or `sheet.html` affects every sheet, not just Holdings.** CSS
scoped with the `#holdings-table` prefix is Holdings-only.

---

## 2. Mental model

Holdings renders **one HTML `<table>`** split top-to-bottom into **four
group-based bands**:

```
CASH          ← accounts,  type != credit_card        (green)
CREDIT CARDS  ← accounts,  type == credit_card         (rose)
LOANS         ← asset_entries, kind == debt            (amber)
ASSETS        ← asset_entries, kind == asset           (blue)
```

Each band is exactly **one `<tbody class="holdings-group" data-group="…">`**.
That one-tbody-per-group rule is load-bearing: `position: sticky` on the heading
and column-header rows is confined to their tbody, which is how each group's
header pins while its own rows scroll and is then replaced by the next group's
header. Never split a group across two tbodies or merge two groups into one.

Every band draws from **exactly one source table** now (Cash/Credit from
`get_accounts`; Loans/Assets from `get_asset_entries`). Because no band mixes
sources, each band reorders and sorts independently.

Each data row carries a `source` (`"account"` or `"asset"`) and a `ref` (the
holding id). The single `update` / `delete` / `cell_edit` routes dispatch on
`source` to the right repository. This is why one table can edit two different
backing tables without special-casing per column.

### Data → display pipeline

`holdings_view` → `_all_rows()` builds every row bucketed by group key →
`_apply_filters()` → `_groups_ctx()` builds the four group dicts + the master
footer → rendered by `holdings_tbody.html`. Inline edits swap **just the tbody**
via HTMX (`_render_tbody`), except add/delete which do a full `HX-Refresh`.

---

## 3. Column layout — shared leading columns, ragged right edge

The first **four columns are shared across all groups** and stay pixel-aligned
via `<colgroup>` fixed widths:

| # | Column | Width | Align |
| --- | --- | --- | --- |
| 0 | Institution | `10rem` | left |
| 1 | Type | `9rem` | left |
| 2 | Name | `14rem` | left |
| 3 | Amount | `9rem` | right |

Each group then appends **its own trailing columns as real `<td>` cells**, giving
a ragged right edge with native table auto-sizing. The full per-group layouts
(source of truth: the `_*_COLS` lists in `holdings.py`):

- **Cash** → Reserve · Funding · As Of
- **Credit Cards** → Limit · Available · Rewards · Statement · Due · Linked · As Of
- **Loans** → Interest · Equity · LTV · Original · Term · Originated · P&I · Paid · Due · Linked · As Of
- **Assets** → Unit Price · Qty · Source · Est. Return · Mo. Contrib.

Right-aligned trailing columns: all the money/number columns (Reserve, Funding,
Limit, Available, Rewards, Statement, Interest, Equity, LTV, Original, Term, P&I,
Paid, Unit Price, Qty, Est. Return, Mo. Contrib.). Left-aligned: As Of, Due,
Originated, Linked, Source.

**Ragged-edge padding.** Loans is the widest band (15 columns total). Shorter
groups pad to `max_cols` with a filler `<td colspan>` so the HTML grid stays
rectangular. In edit mode there is one extra actions column, so
`span = max_cols + 1`.

Every column has a hover tooltip (`_COL_TOOLTIPS` in `holdings.py`); keep the map
in sync when adding a column.

---

## 4. Colour system — accent by group, not by sign

Each group gets **one accent colour**, declared as CSS custom properties on its
`<tbody data-group>` and read by every rule (`--accent`, `--band`, `--band-dark`,
`--label`, `--label-dark`). The whole palette lives at the top of
`holdings.html`.

| Group | `--accent` | `--band` (light) | `--band-dark` | `--label` | `--label-dark` |
| --- | --- | --- | --- | --- | --- |
| cash | `#34d399` emerald | `#ecfdf5` | `#022c22` | `#065f46` | `#6ee7b7` |
| credit | `#fb7185` rose | `#fff1f2` | `#4c0519` | `#9f1239` | `#fda4af` |
| loan | `#fbbf24` amber | `#fffbeb` | `#451a03` | `#92400e` | `#fcd34d` |
| asset | `#3b82f6` blue | `#eff6ff` | `#172554` | `#1e40af` | `#bfdbfe` |

The palette is **temperature-coded**: cool = you own it (Cash emerald, Assets
blue), warm = you owe it (Credit Cards rose, Loans amber). So the old
asset/liability read survives *and* each group has its own identity.

**Accent is fixed by group, never by the current amount's sign.** A credit card
reads as a liability (rose rail) even at a `$0.00` balance. Data rows still carry
a `data-accent="asset|liability"` attribute for semantics, but it no longer
drives colour.

The accent shows as a **continuous 4px left rail** running down the entire group
— heading, column header, every data row, and the add-row — so the group reads as
one block. It is drawn as `box-shadow: inset 4px 0 0 var(--accent)` (see §6 for
why it must be a shadow, not a border). Because the rail eats 4px of the first
cell's padding, the leading cells get `padding-left: 0.625rem` so content clears
the rail by the same ~6px other columns clear their dividers.

---

## 5. Group heading & column-header rows

Each group renders, in order inside its tbody:

1. **Heading band** (`.holdings-group-heading`) — `CASH` / `CREDIT CARDS` / …
   label (uppercase, group-`--label` colour), a `--band` tinted background, and
   the group **subtotal** in the Amount column (reddened if negative). A
   continuous accent line runs along **both** the top and bottom edge of this row
   (box-shadow, full width).
2. **Column header** (`.holdings-group-header`) — the sortable `<th>` cells, on a
   `gray-100`/`gray-700` background with a `gray-300`/`gray-600` 2px underline and
   `gray-200`/`gray-600` 1px inter-column dividers (all box-shadows).
3. **Data rows** — or a single "No <group> yet." / "No … match the current
   filters." spanning row when empty.
4. **Add-row** (edit mode only) — a `+ Add account` / `+ Add credit card` /
   `+ Add loan` / `+ Add asset` link. It **flows inline** as the last row of the
   group; it is *not* pinned.

The heading pins at `top: 0`; the column header pins just beneath it at
`top: var(--sheet-heading-h, 1.75rem)`. `sheet-scroll.js` measures the real
heading height and publishes `--sheet-heading-h` so the two stay flush.

There is **no global `<thead>`** — headers are per-group rows inside each tbody.
Don't add a `<thead>`; the sticky math and the scroll-shadow insets assume none.

---

## 6. The sticky-row border invariant (read this before touching borders)

**This is the single most common way Holdings gets broken.**

These sheets use `border-collapse`. A collapsed border is painted in the
**table's own paint layer**, not the cell's. When a row is `position: sticky`
(the group heading, the column header, the pinned total rows, and the left accent
rail on data rows), a collapsed border on that row **scrolls out of view or
paints over/beside the sticky cell** — reproduced in both WebKit and Chromium.

**Rule: no border on a sticky row (or the accent rail) may be a `border-collapse`
border. Draw it as a `box-shadow` on the cell instead** — a box-shadow paints with
the sticky element and stays put.

Everything below is already a box-shadow for this reason; keep it that way:

- the 4px group accent rail (heading, header, data rows, add-row);
- the heading band's top/bottom accent lines;
- the column-header underline + inter-column dividers;
- the total-row inter-column dividers;
- the shared `<thead>` underline/dividers on the *other* sheets.

If you add a visible line to any sticky row, use `inset` box-shadow, compose it
with any existing shadows on that cell (a single `box-shadow` declaration
replaces the previous one — list all layers together), and add a dark-mode
variant.

---

## 7. z-index map (do not reshuffle casually)

Within `.table-scroll-container` / `#holdings-table`. Each layer was chosen so the
right thing clips over the right thing while scrolling in both axes.

| z | Element | Why |
| --- | --- | --- |
| 4 | `.sheet-scroll-shadow--top` | falls on scrolling data, *under* group chrome & accent rails so they scroll up crisply |
| 5 | data-row accent-rail first cell (`tr[data-accent] > td:first-child`) | lifts the rail above the top shadow but below group chrome & actions |
| 6 | `.table-actions-cell` (data rows) | sticky right column; data scrolls behind it |
| 6 | `.sheet-scroll-shadow` (base) | |
| 7 | `.holdings-group-heading > td`, `.holdings-group-header > th` | group chrome above data + actions |
| 7 | `.total-row` and its actions cell | pinned footer sits above the sticky actions column |
| 8 | `.sheet-scroll-shadow--bottom`, `--left`, `--right` | side/bottom shadows cover the column header & a mid-scroll group name, but their `top` keeps them below the group title band |

(The shared `<thead>` on other sheets is z-10, and the freeze-col corner is z-12,
but Holdings uses neither.)

---

## 8. Cell content, formatting & state colours

- **Row height** is a global `1.75rem` (28px); footer rows match. Inline
  inputs/selects are capped at `1.25rem` so editing never changes row height.
- **Money** is right-aligned with `tabular-nums`. Negatives render in
  parentheses — `($1,580.22)` — and are coloured `text-red-600 dark:text-red-400`.
  "Display negative" is detected by a leading `(` or `-`.
- **Blank cells** show a muted dash `-` in `text-gray-300 dark:text-gray-600` so
  populated cells stand out. (Not every column applies to every row; e.g. Loans'
  Equity/LTV only populate on the debt side of a secured pair.)
- **As Of staleness** colours the date by age: none ≤ 35 days, amber > 35
  (`text-amber-600/400`), red > 95 (`text-red-600/400`). Thresholds:
  `_STALE_AMBER_DAYS = 35`, `_STALE_RED_DAYS = 95`.
- **Whitespace:** the table is `white-space: nowrap`; horizontal scroll is the
  intended overflow behaviour (see §13). Do not wrap cells to avoid scroll.

### Computed / read-only columns (never editable, rendered muted in edit mode)

- **Credit-card Available** = `credit_limit + balance` (signed owed balance).
  Blank when no limit. It is a *cache-free* derivation, so it can never drift from
  the imported balance; editing Limit or Balance recomputes it. It intentionally
  ignores pending holds (reads slightly high) — the accepted tradeoff for never
  drifting.
- **Equity / LTV** show on the **loan** row (the debt side of a secured pair),
  from `calculations.equity_pairs`.
- **P&I** (`scheduled_payment`) and **Paid** (`payoff_progress`) are derived
  amortization values, only for loans of `type == "loan"` with origination inputs.
- **Amount** for a multi-unit asset is computed from Unit Price × Qty; you edit
  Qty, not Amount. Amount is directly editable only for single-USD-unit assets and
  for cash/credit balances.

---

## 9. Master footer — Liquid & Net worth

The table closes with a **two-line sticky footer** in a trailing `<tbody>`:

- **Liquid** (upper) and **Net worth** (bottom), from
  `calculations.tiered_totals`.
- Both stick to the sheet's bottom edge. The upper row is `.total-upper`, pinned
  one data-row height above the bottom (`bottom: 1.75rem`) so it sits directly
  above the bottom-pinned Net worth.
- The label sits in the Name column (index 2); the figure in the Amount column
  (index 3, `amount_pos`).

Liquidity is a **cross-cutting** property (fixed by holding *type*, per
`fintrack/core/types.py`), so it is reported here independently of the row
grouping — it is deliberately *not* one group's subtotal. Group subtotals live in
each heading band; the footer carries only the two totals that matter globally.

Only the total rows pin to the bottom. `sheet-scroll.js` injects a **grid-filler
row** below the last data row and sizes it to push the footer to the sheet's
bottom, filling the gap with a faint horizontal gridline canvas.

---

## 10. Edit mode

Toggled by the header lock (`?edit=1`). In view mode the entire actions column is
**dropped** (not just hidden) so it stops eating horizontal space, especially on
mobile.

- **Actions column** — sticky to the right edge (`.table-actions-cell`, z-6,
  `2.75rem` wide, opaque `white`/`gray-800` background). Holds a drag handle +
  delete icon per row. The total-row's actions cell is filled with the total-band
  colour so the footer styling runs full width.
- **Click-to-edit** — an editable display cell is a `role="button"` that HTMX-GETs
  the tbody with that one cell swapped to an `<input>`/`<select>`; the input POSTs
  to `update` on blur/Enter/change and the tbody re-renders. Selected cell shows a
  2px inset blue ring while focused. On the first column the ring composes with
  the accent rail (`inset 4px 0 0 var(--accent), inset 0 0 0 2px …`).
- **Per-row editability gating.** Not every column is editable on every row:
  - Identity fields (Institution, Type, Name) are always editable — **except a
    loan's Type**, which is fixed to `loan` (its band).
  - Credit-card Balance is always editable (it's the canonical input); Available
    is never editable.
  - Money/detail fields are gated by `account_field_editable` / single-unit rules.
  See `_account_col_fields` / `_asset_col_fields` in `holdings.py`.
- **Add** — `+ Add …` inserts a blank holding in that group (sensible type
  defaults) and does an `HX-Refresh`; the user then edits it inline.
- **Delete** — trash icon → inline Yes/No confirm cell → POST delete.
- **`X-Local-Date`** — every htmx request sends the browser's local date; a manual
  balance edit stamps As Of with the user's day, not the server's timezone.

The web GUI and the Python CLI (`accounts` / `assets` / `debts` command groups)
are both first-class and must stay at parity — a field you make editable here
should have a CLI equivalent and vice versa.

---

## 11. Sorting (per group, client-side)

`holdings-sort.js`. Clicking a group's column header sorts **only that group's**
data rows, cycling asc → desc → none, re-inserting them ahead of the add-row and
subtotal so header/footer stay put. Numeric parsing strips `$ , %` and treats
`(…)` as negative; falls back to locale string compare. State persists in the URL
(`sort_<group>_col` / `sort_<group>_dir`) so it survives reloads and tbody swaps.
While a group is sorted its tbody gets `data-sorted`, which **disables that
group's drag-reorder**.

---

## 12. Reorder (per group, drag)

`row-reorder.js` + the `reorder` route. Each group's data rows live in their own
`<tbody data-reorder-url>`, so dragging is scoped per group. The posted `order` is
a permutation of the group's **local** 0-based positions; the route maps it onto
the group's **global** slots in the shared source table, leaving the other group's
rows fixed, and persists a full-table permutation. Drag is disabled while the
group is sorted (`data-sorted`) or while a filter is active
(`data-reorder-locked`) — reordering against a partial view would corrupt
positions.

---

## 13. Scroll shadows, freeze & responsiveness

- **Four-edge scroll shadows** (opt-in via `data-sheet-scroll`).
  `sheet-scroll.js` wraps the scroll container in a non-scrolling
  `.sheet-scroll-frame` and appends four overlay divs to the *frame* (not the
  scrolling element, or they'd drift). They fade in when content is hidden past
  that edge. Insets: top starts below the pinned header, bottom above the pinned
  footer, right left-of the sticky actions column (`--sheet-right-h`), and the
  side shadows start below the group **title band** (`--sheet-heading-h`) so they
  cover the column header but never the group name.
- **Spreadsheet-first / horizontal scroll is fine.** Information density is a
  feature here, not a problem to design around. Favour showing fields over hiding
  them; do not add expand/detail drawers to reclaim width. Wide content scrolls
  inside `.table-scroll-container`; the page body itself never scrolls sideways.
- **Responsive.** On narrow viewports the header collapses to a hamburger and the
  filter bar collapses into a "Filters" accordion; the table scrolls horizontally
  with the sticky footer still pinned. The navigation is one responsive Alpine
  component on `<body>`, not a separate mobile nav.

---

## 14. Filters

A GET form (not HTMX) with three multi-selects: **Type**, **Balance**
(Assets / Liabilities, keyed by sign of contribution), **Institution**. Options
are computed from the rows actually present. `active_count` counts a facet as
active only when a strict, non-empty subset is selected (all-or-none doesn't
count). Filtering sets `filters_active`, which switches the empty-group message to
"No … match the current filters."

---

## 15. States to test on any change

The seed script (`scripts/seed_example.py`) creates three demo households for
exactly this:

- **Dense Household** — overflows every band; use it to check scrolling, sticky
  headers/footer, per-group sort/reorder, the ragged right edge, and all four
  colour bands.
- **Sparse Household** — a handful of rows; the minimal-but-nonempty case.
- **Empty Household** — every band shows its heading (with `$0.00` subtotal), its
  full column header, a "No … yet." row, and (in edit mode) the add-row; the
  footer reads `$0.00 / $0.00`. The grid canvas fills the empty space.

Seed locally with `FINTRACK_DB=preview.db uv run python scripts/seed_example.py`
then `mise run serve`.

---

## 16. Invariants checklist (don't merge a Holdings change that violates these)

1. **One `<tbody class="holdings-group">` per group.** Sticky headers depend on
   it. No `<thead>`.
2. **Sticky-row borders are box-shadows, never `border-collapse` borders** — group
   heading edges, column-header underline/dividers, total-row dividers, the accent
   rail. Add dark-mode variants.
3. **The 4px accent rail is continuous** across heading, header, every data row,
   and the add-row, and is composed (not overwritten) with any other shadow on the
   first cell.
4. **Accent colour is fixed by group, not by amount sign.** A zero-balance credit
   card is still rose.
5. **The four leading columns stay aligned** via `<colgroup>`; new columns are
   appended as trailing `<td>`s and shorter groups pad to `max_cols`.
6. **z-index order per §7** — changing one layer usually breaks clipping of
   another while scrolling. Test scroll in both axes, in both themes.
7. **Computed columns stay read-only** — CC Available, Equity/LTV, P&I, Paid, and
   multi-unit Amount are never editable.
8. **Only the total rows pin to the bottom;** the add-row flows inline.
9. **Per-group scope holds** for sort and reorder; both are disabled on a filtered
   or sorted view where positions would be ambiguous.
10. **GUI ↔ CLI parity** — a capability added here has a CLI counterpart.
11. **Verify in light + dark, view + edit, dense + empty**, scrolled to each edge,
    before pushing. Run `mise run test` (format, lint, unit, e2e).

---

## 17. Deliberate tradeoffs (not bugs — don't "fix" these)

- Credit-card **Available ignores pending holds**, so it reads slightly high. The
  tradeoff buys never drifting from the imported balance.
- **Liquid** in the footer is not any group's subtotal; liquidity is fixed by
  holding type and cuts across the group bands.
- **Density over decluttering.** Blank cells and a wide right edge are expected;
  they exist to expose known fields for entry and completeness checking.
- **Loans is the widest band**, so most groups show a large padded right region.
  That raggedness is intentional (native auto-sizing beats blank padding columns
  that would misalign the shared leading four).

---

## 18. Provisional / open questions (NOT locked spec)

These are current behaviours captured so they aren't lost, but flagged as
**unsettled** — describe-don't-enshrine. Each is a question for the table's owner;
until answered, don't treat these as contract or build hard dependencies on them.
When one is confirmed, promote it into the relevant section above (and into
§16/§17 if it becomes an invariant/tradeoff); when one is changed, this is the
list that says it was fair game.

1. **Four different interaction mechanisms in one table.** Cell **edits** do an
   HTMX partial-tbody swap; **Add** and **Delete** do a full-page `HX-Refresh`;
   **Filters** are a plain non-HTMX GET form submit; **Sort** is client-side JS
   with state in the URL and never touches the server. Is this mix intended, or
   should they converge on one model (e.g. HTMX throughout)? The full-refresh on
   Add/Delete in particular is heavier than the edit path.
2. **Sort is client-only and ephemeral; reorder is server-persisted.** A
   column sort reorders rows in the DOM and survives only via URL query params,
   while a drag-reorder writes a permutation to the DB. Sorting a group also
   disables its drag. Is client-only sort the permanent design, or a stopgap
   until sort persists like reorder does?
3. **The Balance filter (Assets / Liabilities) overlaps the group bands.** Cash +
   Assets are always the asset side; Credit Cards + Loans always the liability
   side — so the filter is close to redundant with the four bands it sits above.
   Is it earning its place, or a leftover from the pre-grouping Assets sheet?
4. **Asset Amount editability rule.** Amount is inline-editable only for
   single-unit USD assets and cash/credit balances; multi-unit or symbol-priced
   assets compute Amount (you edit Qty, and the live price feed fills Unit Price).
   This is subtle and easy to trip over — is the single-USD-unit gate the intended
   long-term rule now that the price feed (`fintrack/networth/prices.py`, real
   CoinGecko/Yahoo lookups) is live?
5. **Equity / LTV live on the loan row only**, not mirrored on the secured
   asset's row. Intended (report the pair once, on the debt side), or should the
   asset row also surface its LTV?
6. **Staleness thresholds are magic numbers** — As Of goes amber > 35 days, red
   > 95 (`_STALE_AMBER_DAYS` / `_STALE_RED_DAYS` in `holdings.py`). Are those the
   values we want, and should they be configurable rather than hard-coded?
7. **`data-accent` on data rows is half-retired.** It no longer drives colour
   (group `--accent` does) but is still the selector that lifts a data row's first
   cell to z-5. Keep it as a "this is a data row" hook, or replace it with a
   clearer class so nobody re-wires colour onto it by mistake?

### Doc reconciliation (fix separately)

- DESIGN.md's Holdings section still says *"a group whose rows span two tables is
  left non-reorderable rather than permuting both at once."* After the
  loans-into-their-own-band change, **no band spans two tables** and every band is
  reorderable (`reorderable: True` for all four in `_groups_ctx`). That sentence
  is now describing a case that can't occur; it should be updated when DESIGN.md
  is next touched.
