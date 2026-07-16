"""Table builders for status and subcommands (CLI and web)."""

from decimal import Decimal
from typing import Any, Dict, List

from .calculations import (
    _ACCOUNT_TYPE_TO_CALCULATION,
    _amount_annual,
    _budget_entry_in_month,
    _entry_subtotal,
    _semiannual_other_month,
    _subtotal_remainder_of_month,
)
from .formatting import (
    fmt_day_ordinal,
    fmt_money,
    fmt_month_short,
    fmt_qty,
    fmt_recurrence_display,
    fmt_type_display,
)


def _expected_day_or_date(entry: Dict[str, Any]) -> str:
    """Return expected day (dayOfMonth), month/day, or date for display in table."""
    rec = entry.get("recurrence", "")
    if rec == "monthly":
        dom = entry.get("dayOfMonth")
        return fmt_day_ordinal(dom) if dom is not None else "-"
    if rec == "annual":
        m = entry.get("month")
        dom = entry.get("dayOfMonth")
        doy = entry.get("dayOfYear")
        day_val = (
            dom if dom is not None else doy
        )  # dayOfMonth is canonical; dayOfYear is legacy
        if m is not None and day_val is not None:
            return f"{fmt_month_short(m)} {fmt_day_ordinal(day_val)}"
        return "-"
    if rec == "quarterly":
        m = entry.get("month")
        dom = entry.get("dayOfMonth")
        if m is not None and dom is not None:
            return f"{fmt_month_short(m)} {fmt_day_ordinal(dom)}"
        return "-"
    if rec == "semiannual":
        m = entry.get("month")
        dom = entry.get("dayOfMonth")
        if m is not None and dom is not None:
            other = _semiannual_other_month(m)
            return f"{fmt_month_short(m)} & {fmt_month_short(other)} {fmt_day_ordinal(dom)}"
        return "-"
    if rec == "one_time":
        d = entry.get("date")
        return str(d) if d else "-"
    return "-"  # biweekly or unknown


def _expected_display(entry: Dict[str, Any]) -> str:
    """Combined Expected + Continuous for table display: 'continuous' when monthly+continuous, else day/date."""
    if entry.get("continuous") and entry.get("recurrence") == "monthly":
        return "continuous"
    return _expected_day_or_date(entry)


def _append_table_separator_and_total(
    rows: List[List[Any]],
    headers: List[str],
    total_row: List[Any],
) -> None:
    """Append a separator row and total row to rows. Mutates rows in place."""
    all_cells = [headers] + rows + [total_row]
    col_widths = [
        max(len(str(all_cells[r][c])) for r in range(len(all_cells)))
        for c in range(len(headers))
    ]
    separator_row = ["-" * w for w in col_widths]
    rows.append(separator_row)
    rows.append(total_row)


def _account_display_by_id(accounts: List[Dict[str, Any]]) -> Dict[int, str]:
    """Build map account id -> display name (institution + name + partial)."""
    result = {}
    for a in accounts:
        aid = a.get("id")
        if aid is None:
            continue
        parts = [a.get("institution")] if a.get("institution") else []
        parts.append(a.get("name", "-"))
        if a.get("partial_account_number"):
            parts.append(f"[{a.get('partial_account_number')}]")
        result[aid] = " ".join(parts)
    return result


def _build_accounts_table(
    accounts: List[Dict[str, Any]],
    n2: float,
    show_id: bool = False,
    account_display_by_id: Dict[int, str] | None = None,
) -> tuple:
    """Build (headers, rows) for the liquid/accounts table. Rows include separator and total."""
    account_display_by_id = account_display_by_id or {}
    headers = [
        "Institution",
        "Type",
        "Account",
        "Balance",
        "Limit",
        "Available",
        "Rewards",
        "Statement",
        "Due",
        "Payment account",
    ]
    if show_id:
        headers = ["ID"] + headers
    rows = []
    for a in accounts:
        account_id = a.get("id")
        institution = a.get("institution")
        name = a.get("name", "-")
        partial = a.get("partial_account_number")
        name_display = name
        if partial:
            name_display = f"{name} [{partial}]"
        institution_display = institution or "-"
        rewards = a.get("rewards_balance")
        stmt_bal = a.get("statement_balance")
        stmt_due = a.get("statement_due_day_of_month")
        pay_ref = a.get("paymentAccountRef")
        pay_display = (
            account_display_by_id.get(pay_ref, "-") if pay_ref is not None else "-"
        )
        if _ACCOUNT_TYPE_TO_CALCULATION.get(a.get("type")) == "credit_card":
            limit = a.get("limit")
            available = a.get("available")
            if limit is not None and available is not None:
                balance_owed = available - limit
                balance_with_rewards = balance_owed + (
                    rewards if rewards is not None else 0
                )
                row = [
                    institution_display,
                    fmt_type_display(a.get("type") or "-"),
                    name_display,
                    fmt_money(balance_with_rewards),
                    fmt_money(limit),
                    fmt_money(available),
                    fmt_money(rewards) if rewards is not None else "-",
                    fmt_money(stmt_bal) if stmt_bal is not None else "-",
                    fmt_day_ordinal(stmt_due) if stmt_due is not None else "-",
                    pay_display,
                ]
            else:
                row = [
                    institution_display,
                    fmt_type_display(a.get("type") or "-"),
                    name_display,
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    pay_display,
                ]
        else:
            bal = a.get("balance", 0)
            row = [
                institution_display,
                fmt_type_display(a.get("type") or "-"),
                name_display,
                fmt_money(bal),
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
            ]
        if show_id:
            row = [str(account_id) if account_id is not None else "-"] + row
        rows.append(row)
    total_row = ["Total", "", "", fmt_money(n2), "-", "-", "-", "-", "-", "-"]
    if show_id:
        total_row = [""] + total_row
    _append_table_separator_and_total(rows, headers, total_row)
    return (headers, rows)


def _build_budget_table(
    budget: List[Dict[str, Any]],
    year: int,
    month: int,
    day: int,
    account_display_by_id: Dict[int, str] | None = None,
    show_index: bool = False,
) -> tuple:
    """Build (headers, rows) for budget table (unified income+expenses). Rows include separator and total.
    Shows prorated subtotal (remainder of month), monthly budget amount, and annual budget amount.
    budget is a list of entries with kind: income|expense.
    """
    account_display_by_id = account_display_by_id or {}
    headers = [
        "Kind",
        "Type",
        "Description",
        "Amount",
        "Recurrence",
        "When",
        "Remaining",
        "Monthly",
        "Annual",
        "Auto account",
    ]
    if show_index:
        headers = ["Index"] + headers

    def _auto_account(entry: Dict[str, Any]) -> str:
        ref = entry.get("autoAccountRef")
        return account_display_by_id.get(ref, "-") if ref is not None else "-"

    rows = []
    total_subtotal = Decimal("0")
    total_monthly = Decimal("0")
    total_annual = Decimal("0")
    for idx, e in enumerate(budget):
        kind = e.get("kind", "income")
        sign = 1 if kind == "income" else -1
        st = _subtotal_remainder_of_month(e, year, month, day)
        monthly_amt = _budget_entry_in_month(e, year, month, day=0)
        annual_amt = _amount_annual(e)
        total_subtotal += sign * st
        total_monthly += sign * monthly_amt
        total_annual += sign * annual_amt
        row = [
            kind.capitalize(),
            fmt_type_display(e.get("type") or "-"),
            e.get("description", "-"),
            fmt_money(sign * e.get("amount", 0)),
            fmt_recurrence_display(e.get("recurrence") or "-"),
            _expected_display(e),
            fmt_money(sign * st),
            fmt_money(sign * monthly_amt),
            fmt_money(sign * annual_amt),
            _auto_account(e),
        ]
        if show_index:
            row = [str(idx)] + row
        rows.append(row)
    num_cols = len(headers)
    total_row = [""] * num_cols
    total_row[0] = "Total"
    offset = 1 if show_index else 0
    total_row[6 + offset] = fmt_money(total_subtotal)
    total_row[7 + offset] = fmt_money(total_monthly)
    total_row[8 + offset] = fmt_money(total_annual)
    _append_table_separator_and_total(rows, headers, total_row)
    return (headers, rows)


def _build_funding_table(funding_results: List[Dict[str, Any]]) -> tuple:
    """Build (headers, rows) for the account funding needed table.

    Columns: Account | Balance | CC Statements | Direct Expenses | Reserve |
             Funding Needed | Surplus
    """
    headers = [
        "Institution",
        "Type",
        "Account",
        "Balance",
        "CC Statements",
        "Direct Expenses",
        "Reserve",
        "Funding Needed",
    ]
    rows = []
    for r in funding_results:
        acc = r["account"]
        rows.append(
            [
                acc.get("institution") or "-",
                fmt_type_display(acc.get("type") or "-"),
                acc.get("name", "-"),
                fmt_money(r["balance"]),
                fmt_money(r["cc_total"]) if r["cc_total"] > 0 else "-",
                fmt_money(r["expenses_total"]) if r["expenses_total"] > 0 else "-",
                fmt_money(r["reserve"]) if r["reserve"] > 0 else "-",
                fmt_money(r["funding_needed"]) if r["funding_needed"] > 0 else "-",
            ]
        )
    return (headers, rows)


def _build_net_worth_table(
    assets: List[Dict[str, Any]], show_index: bool = False
) -> tuple:
    """Build (headers, rows) for unified assets/debts table. Rows include separator and total."""
    headers = [
        "Kind",
        "Institution",
        "Name",
        "Value",
        "Qty",
        "Subtotal",
        "Reference",
        "Interest rate",
    ]
    if show_index:
        headers = ["Index"] + headers

    def _fmt_interest_rate(rate: float | None) -> str:
        if rate is None:
            return "-"
        return f"{rate * 100:.2f}%"

    asset_by_id = {
        e["id"]: e
        for e in assets
        if e.get("kind") == "asset" and e.get("id") is not None
    }
    rows = []
    total_subtotal = Decimal("0")
    for idx, entry in enumerate(assets):
        kind = entry.get("kind", "asset")
        qty = entry.get("quantity")
        subtotal = _entry_subtotal(entry)
        if kind == "asset":
            total_subtotal += subtotal
            row = [
                "Asset",
                entry.get("institution") or "-",
                entry.get("name", "-"),
                fmt_money(entry.get("value", 0)),
                fmt_qty(qty),
                fmt_money(subtotal),
                entry.get("source") or "-",
                "-",
            ]
        else:  # debt
            total_subtotal -= subtotal
            ref = entry.get("assetRef")
            ref_display = "-"
            if ref is not None:
                linked = asset_by_id.get(ref)
                ref_display = linked.get("name", "-") if linked else str(ref)
            row = [
                "Debt",
                entry.get("institution") or "-",
                entry.get("name", "-"),
                fmt_money(entry.get("balance", 0)),
                fmt_qty(qty),
                fmt_money(-subtotal),
                ref_display,
                _fmt_interest_rate(entry.get("interestRate")),
            ]
        if show_index:
            row = [str(idx)] + row
        rows.append(row)
    total_row = ["Total", "-", "-", "-", "-", fmt_money(total_subtotal), "-", "-"]
    if show_index:
        total_row = [""] + total_row
    _append_table_separator_and_total(rows, headers, total_row)
    return (headers, rows)
