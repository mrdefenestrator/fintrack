# Spending deviation flagging — implementation proposal (QA issue 14)

Status: **approved direction, not yet implemented.** This documents the
recommended design so implementation can proceed later without re-deriving
it. Written 2026-07-17 after the QA-fixes round.

## Problem

There is currently no feature that flags when spending in a category (or at
a merchant) deviates from expectations. The user wants early warning
("Dining is running hot this month") and, eventually, a budget-check that
compares actuals against planned budget entries.

## Recommendation in one paragraph

Compute a per-category monthly **baseline** (median of the trailing 12
finished months, from the same query infrastructure the Trends page already
uses) and surface **deviation chips** on the Trends page when the current
month is materially off baseline. No new tables, no configuration, no
background jobs in phase 1 — it is a presentation-layer feature over
existing data. Budget-aware checks come in phase 2 by joining
`budget_entries.category` (which already exists precisely to link planned
entries to ledger categories — see the comment in
`fintrack/core/models.py`). Merchant-level deviations are phase 3 and
should wait for real demand.

## Phase 1 — category deviation chips on Trends

### Baseline

- For each category: `baseline = median(monthly total over the trailing 12
  *finished* months)`, excluding months with zero transactions for that
  category (a category that only existed for 4 months gets a 4-month
  median; require >= 3 non-zero months before flagging at all).
- Median, not mean: one annual insurance bill must not poison the baseline.
- Data source: the monthly totals already computed for the Trends table
  (`get_monthly_totals_range` in the ledger repository). The baseline is
  derivable from the same result set the page already loads — do NOT add a
  second query pass; compute in the route/helper from the fetched grid.
- The current (partial) month is compared **prorated**: compare
  `actual_so_far` against `baseline * (day_of_month / days_in_month)`.
  Without proration every month looks fine until the 25th and then
  explodes; with it, flags appear early, which is the entire point.

### Deviation definition

Flag a category for the displayed month when BOTH:

- relative: `actual > 1.4 × expected` (or `< 0.6 ×` for an under-spend
  badge, visually distinct and quieter), AND
- absolute: `|actual − expected| >= $50`.

The absolute floor prevents "$4 vs $2 coffee" noise; the pair of thresholds
works without configuration. Hard-code them as module constants first
(`DEVIATION_RATIO = 1.4`, `DEVIATION_FLOOR = Decimal("50")`); make them
user-tunable only if real use demands it.

### UI

- On the Trends table: a small chip next to the category name in the
  flagged month's cell (or the row header when the flagged month is the
  window's last month) — e.g. `▲ 62% over usual`, amber for over, muted
  blue for under. Tooltip: "Median of last 12 months: $312. This month
  (prorated): $505."
- Respect the existing window paging (`end=` param): chips are computed for
  whatever window is displayed, using the 12 months trailing that window's
  anchor — the baseline logic must take the anchor date, not `today`.
- Excluded categories (the `trends_excluded` section) get no chips.
- Keep it out of the header/nav; this is a Trends-page feature until
  phase 2.

### Testing

- Pure-function unit tests for baseline + proration + thresholds (feed a
  synthetic 13-month grid; assert flag/no-flag at the boundaries).
- One route test asserting chips render for a seeded over-spend and absent
  for normal months.
- E2e: one test that a seeded deviation shows the chip text. (Follow
  existing tests/spending/test_web/test_trends.py and
  tests/e2e/ledger/test_trends.py patterns; no generic button[type=submit]
  selectors.)

## Phase 2 — budget check

Once phase 1 chips exist, extend the comparison source: where a category
has a matching planned amount (sum of `budget_entries` rows carrying that
`category`, normalized to a monthly figure via the existing
recurrence/proration engine in `fintrack/budget/`), compare actuals against
**budget** instead of the trailing median, and label the chip accordingly
(`▲ $120 over budget`). Categories without budget entries keep the
baseline behavior. This reuses the phase-1 chip UI wholesale — only the
`expected` input changes.

Open design question to settle at implementation time: whether a category
with both a budget and a wildly different historical median should show one
chip (budget wins — recommended; budget is an explicit statement of
intent) or both.

## Phase 3 (deferred) — merchant-level deviations & subscriptions

Per-merchant baselines are noisy (most merchants are irregular). The useful
subset is *recurring* merchants (subscriptions): flag when a known-regular
merchant's amount changes (price hike) or a month is missed/doubled.
Detection: merchants with >= 4 transactions at roughly equal intervals and
amounts (coefficient of variation < 0.15). This is a separate feature with
its own UI home (probably Merchants page) — do not bolt it onto phase 1.

## Explicitly rejected alternatives

- **A deviations table / stored alerts** — nothing needs persistence; all
  signals are recomputable from transactions + budget_entries. Revisit only
  if alert acknowledgement ("mute this flag") becomes a requirement.
- **Mean-based baselines or z-scores** — 12 points is too few for a stable
  σ; median + ratio floor is more robust and explainable.
- **A new top-level page** — chips belong where the eyes already are
  (Trends). If phase 2 grows a "review all flags" need, the removed Status
  page's slot ("overview/home with decisions on it") is the natural
  landing place — see the QA discussion notes in issues.md items 6/14.
