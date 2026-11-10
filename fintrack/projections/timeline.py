"""Timeline: actual past month-ends and projected future months in one window.

Projections shows a sliding window of months (#98). Months before the current
one show actual month-end values — each holding's latest ``balance_history``
point on or before the month's last day (``month_end_points``). The current
month and later show the projection (``engine.project``), run from now through
the window's end. A "Now" value (each holding's current balance) sits between
the two when the window spans today.

Past cells can be:
- **missing** (None): the holding has no point yet — blank, not zero, and the
  band subtotal / totals that month are marked partial;
- **carried forward**: the latest point is from an earlier month (``carried``),
  so the value is the last one recorded, not one recorded that month.

Changes view: a month's change is its value minus the previous month's, except
the current month, which (as before) is measured from Now — what's still to
come this month. Missing on either side means no change shown for that row;
subtotals and totals sum the rows that have one (``change_totals``) and say
they're partial.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection

from fintrack.accounts.balance_history import earliest_point_date, month_end_points
from fintrack.budget.recurrence import month_sequence
from fintrack.networth.calculations import liquid_minus_cc, liquid_total
from fintrack.projections import engine

_ZERO = Decimal(0)

WINDOW_CHOICES = (6, 12, 24)


def month_offset(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Months from a to b (b - a)."""
    return (b[0] * 12 + b[1]) - (a[0] * 12 + a[1])


def shift_month(ym: tuple[int, int], offset: int) -> tuple[int, int]:
    """(year, month) moved by `offset` months (either direction)."""
    total = ym[0] * 12 + (ym[1] - 1) + offset
    return total // 12, total % 12 + 1


def parse_month(raw: str | None) -> tuple[int, int] | None:
    """'YYYY-MM' → (year, month); None for anything else."""
    if not raw:
        return None
    try:
        year, month = (int(p) for p in raw.split("-"))
    except ValueError:
        return None
    if not 1 <= month <= 12 or not 1900 <= year <= 9999:
        return None
    return year, month


def month_key(ym: tuple[int, int]) -> str:
    return f"{ym[0]:04d}-{ym[1]:02d}"


def clamp_start(
    start: tuple[int, int], months: int, current: tuple[int, int]
) -> tuple[int, int]:
    """Keep the window's end within the projection horizon (MAX_MONTHS months
    from the current month, inclusive)."""
    last_allowed = shift_month(current, engine.MAX_MONTHS - 1)
    end = shift_month(start, months - 1)
    over = month_offset(last_allowed, end)
    return shift_month(start, -over) if over > 0 else start


def _contributes_to_liquid(account: dict[str, Any]) -> bool:
    return liquid_total([{**account, "balance": Decimal(1)}]) != _ZERO


def _contributes_to_net_worth(account: dict[str, Any]) -> bool:
    return liquid_minus_cc([{**account, "balance": Decimal(1)}]) != _ZERO


def _cell(value: Decimal | None, **extra: Any) -> dict[str, Any]:
    return {"value": value, "carried": False, "as_of": None, "below": False, **extra}


def _total(value: Decimal | None, missing: int = 0) -> dict[str, Any]:
    return {"value": value, "missing": missing}


def build_timeline(
    conn: Connection,
    snapshot_id: int,
    *,
    start: tuple[int, int] | None = None,
    months: int = engine.DEFAULT_MONTHS,
    include_estimate: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """The window of `months` months starting at `start` (default: the current
    month): per row, past actuals then projected values, plus band-independent
    Liquid / Net worth totals and the summary line's numbers."""
    today = today or datetime.now().astimezone().date()
    current = (today.year, today.month)
    months = engine.clamp_months(months)
    start = clamp_start(start or current, months, current)
    grid = month_sequence(*start, months)
    end = grid[-1]

    # Project from now through the window's end (at least the current month,
    # so "Now" and the estimate exist even for an all-past window).
    horizon = max(1, month_offset(current, end) + 1)
    result = engine.project(
        conn,
        snapshot_id,
        months=horizon,
        include_estimate=include_estimate,
        today=today,
    )

    # Past months in the window, plus the month before it (the first column's
    # change is measured against it).
    before = shift_month(start, -1)
    past_months = [ym for ym in [before, *grid] if month_offset(current, ym) < 0]
    holding_ids = [r["account"]["id"] for r in result["rows"]]
    points = month_end_points(conn, holding_ids, past_months)
    past_pos = {ym: i for i, ym in enumerate(past_months)}

    def value_at(row: dict, ym: tuple[int, int]) -> dict[str, Any]:
        off = month_offset(current, ym)
        if off >= 0:
            return _cell(row["balances"][off], below=row["below"][off], future=True)
        point = points.get(row["account"]["id"], [None] * len(past_months))[
            past_pos[ym]
        ]
        if point is None:
            return _cell(None, future=False)
        first = date(ym[0], ym[1], 1)
        return _cell(
            point["balance"],
            carried=point["as_of"] < first,
            as_of=point["as_of"],
            future=False,
        )

    month_infos = []
    for ym in grid:
        off = month_offset(current, ym)
        month_infos.append(
            {
                "year": ym[0],
                "month": ym[1],
                "key": month_key(ym),
                "label": engine._month_label(*ym),
                "past": off < 0,
                "current": off == 0,
                # Index into the projection's months (breakdown), None if past.
                "index": off if off >= 0 else None,
            }
        )

    starts_now = month_offset(current, start) == 0
    rows = []
    for row in result["rows"]:
        cells = [value_at(row, ym) for ym in grid]
        # The first column's change is measured from the month before — or
        # from Now when the window starts at the current month.
        opening = row["start"] if starts_now else value_at(row, before)["value"]
        is_account = row.get("_source") is None
        rows.append(
            {
                **row,
                "cells": cells,
                "opening": opening,
                # Which totals the row counts toward (same formulas as the
                # engine's: liquid_total, and liquid_minus_cc + assets - loans).
                "in_liquid": is_account and _contributes_to_liquid(row["account"]),
                "in_net_worth": not is_account
                or _contributes_to_net_worth(row["account"]),
            }
        )

    # Unassigned: budget flows with no account — projected only (no history).
    unassigned = None
    if result["has_unassigned"]:
        u = result["unassigned"]

        def u_at(ym):
            off = month_offset(current, ym)
            return _cell(u[off], future=True) if off >= 0 else _cell(None)

        unassigned = {
            "cells": [u_at(ym) for ym in grid],
            "opening": _ZERO if starts_now else u_at(before)["value"],
        }

    def totals_at(ym: tuple[int, int], values: list) -> tuple[dict, dict]:
        """Liquid and net worth for one month: the projection's for the
        current month on, else summed from the holdings' month-end values
        (missing holdings counted, so the total can say it's partial)."""
        off = month_offset(current, ym)
        if off >= 0:
            return _total(result["liquid"][off]), _total(result["net_worth"][off])
        accounts, missing_liquid, missing_any = [], 0, 0
        holdings_sum = _ZERO
        for row, v in zip(result["rows"], values, strict=True):
            is_account = row.get("_source") is None
            if v is None:
                missing_any += 1
                if is_account and _contributes_to_liquid(row["account"]):
                    missing_liquid += 1
            elif is_account:
                accounts.append({**row["account"], "balance": v})
            else:
                holdings_sum += v  # assets positive, loans already negative
        return (
            _total(liquid_total(accounts), missing_liquid),
            _total(liquid_minus_cc(accounts) + holdings_sum, missing_any),
        )

    liquid, net_worth = [], []
    for j, ym in enumerate(grid):
        lt, nt = totals_at(ym, [r["cells"][j]["value"] for r in rows])
        liquid.append(lt)
        net_worth.append(nt)
    earliest = earliest_point_date(conn, holding_ids)
    earliest_ym = (earliest.year, earliest.month) if earliest else current
    now_pos = next((j for j, m in enumerate(month_infos) if m["current"]), None)

    return {
        "today": today,
        "current": current,
        "start": start,
        "end": end,
        "months_count": months,
        "label": f"{month_infos[0]['label']} – {month_infos[-1]['label']}",
        "months": month_infos,
        "now_pos": now_pos,
        "has_past": any(m["past"] for m in month_infos),
        "has_future": any(not m["past"] for m in month_infos),
        "rows": rows,
        "unassigned": unassigned,
        "liquid": liquid,
        "net_worth": net_worth,
        "projection": result,
        "summary": _summary(result, month_infos, liquid, net_worth),
        # Paging: nothing recorded before `earliest`, and the projection stops
        # MAX_MONTHS out.
        "can_go_back": month_offset(earliest_ym, start) > 0,
        "can_go_forward": month_offset(end, shift_month(current, engine.MAX_MONTHS - 1))
        > 0,
        "prev_start": shift_month(start, -months),
        "next_start": shift_month(start, months),
    }


def changes(cells: list[Any], opening: Any, months: list[dict], now: Any) -> list:
    """Month-over-month change for a row of values (Decimal or None, or cell
    dicts with "value"). The current month is measured from `now`; any other
    month from the month before (the first column from `opening`)."""

    def val(c):
        return c["value"] if isinstance(c, dict) else c

    out = []
    prev = val(opening)
    for c, m in zip(cells, months, strict=True):
        cur = val(c)
        base = val(now) if m["current"] else prev
        out.append(None if cur is None or base is None else cur - base)
        prev = cur
    return out


def change_totals(tl: dict) -> dict[str, list[dict[str, Any]]]:
    """Liquid and net-worth change per month, like for like: the sum of the
    changes of the rows counted in that total, over the rows that have a value
    in both months (``missing`` counts the rest, so a past total can say it's
    partial instead of going blank). From the current month on every row has
    a value, so this is exactly the projection's total change."""
    lines = [
        (changes(r["cells"], r["opening"], tl["months"], r["start"]), r)
        for r in tl["rows"]
    ]
    u = tl["unassigned"]
    if u is not None:
        unassigned_row = {"in_liquid": True, "in_net_worth": True}
        lines.append(
            (changes(u["cells"], u["opening"], tl["months"], _ZERO), unassigned_row)
        )
    out: dict[str, list[dict[str, Any]]] = {}
    for key, flag in (("liquid", "in_liquid"), ("net_worth", "in_net_worth")):
        cells = []
        for j in range(len(tl["months"])):
            deltas = [d[j] for d, r in lines if r[flag]]
            present = [d for d in deltas if d is not None]
            missing = len(deltas) - len(present)
            cells.append(
                {
                    "value": sum(present, _ZERO) if present else None,
                    "missing": missing if present else 0,
                }
            )
        out[key] = cells
    return out


def _summary(result, month_infos, liquid, net_worth) -> dict[str, Any]:
    """Liquid and net worth at the end of the window. A window ending in the
    future uses the projection's summary (change from now); one ending in the
    past shows the actual values then and the change since (to now)."""
    last = month_infos[-1]
    if not last["past"]:
        s = engine.summarize(result)
        return {**s, "past": False}
    out: dict[str, Any] = {"past": True, "end_label": last["label"]}
    for key, totals in (("liquid", liquid), ("net_worth", net_worth)):
        t = totals[-1]
        now = result[f"start_{key}"]
        out[key] = {
            "end": t["value"],
            "change": now - t["value"],
            "partial": t["missing"] > 0,
        }
    return out
