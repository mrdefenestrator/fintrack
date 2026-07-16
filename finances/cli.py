"""CLI for finances tracker."""

import argparse
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

from tabulate import tabulate

from .calculations import (
    _ACCOUNT_TYPE_TO_CALCULATION,
    account_funding_needed,
    liquid_minus_cc,
    net_nonliquid_total,
    projected_change_to_eom,
)
from .db import get_engine, init_db
from .filters import (
    apply_budget_filters,
    filter_accounts_by_type,
    filter_assets_by_kind,
)
from .formatting import fmt_money
from .loader import load_finances_from_db
from .repository import accounts as repo_accounts
from .repository import assets as repo_assets
from .repository import budget as repo_budget
from .repository.snapshots import get_snapshot_id, list_snapshots
from .tables import (
    _account_display_by_id,
    _append_table_separator_and_total,
    _build_accounts_table,
    _build_budget_table,
    _build_funding_table,
    _build_net_worth_table,
)


def _sort_items(
    items: List[Dict[str, Any]], sort_key: str, reverse: bool = False
) -> List[Dict[str, Any]]:
    """Sort items by a given key. Returns new sorted list."""

    def get_sort_value(item):
        val = item.get(sort_key)
        # Handle None values - sort them last
        if val is None:
            return (1, "")
        # Handle numeric values
        if isinstance(val, (int, float, Decimal)):
            return (0, val)
        # Handle string values (case-insensitive)
        return (0, str(val).lower())

    try:
        return sorted(items, key=get_sort_value, reverse=reverse)
    except (KeyError, TypeError):
        print(
            f"Warning: Unable to sort by '{sort_key}', displaying unsorted",
            file=sys.stderr,
        )
        return items


def cmd_status(args: argparse.Namespace) -> int:
    """Show current financial status."""
    data = load_finances_from_db(args.conn, args.snapshot_id)

    accounts = data.get("accounts") or []
    budget = data.get("budget") or []
    assets = data.get("assets") or []

    today = date.today()
    year, month = today.year, today.month

    n2 = liquid_minus_cc(accounts)
    n3 = projected_change_to_eom(budget, year, month, today.day)
    n6 = net_nonliquid_total(assets)

    # Status: totals from accounts, budget, and assets, plus combined total
    status_rows = [
        ["Accounts", fmt_money(n2)],
        ["Budget (prorated)", fmt_money(n3)],
        ["Assets", fmt_money(n6)],
    ]
    headers = ["Kind", "Amount"]
    total_row = ["Total", fmt_money(n2 + n3 + n6)]
    _append_table_separator_and_total(status_rows, headers, total_row)
    print(
        tabulate(
            status_rows,
            headers=headers,
            tablefmt="simple",
            colalign=("left", "right"),
        )
    )

    return 0


def cmd_accounts(args: argparse.Namespace) -> int:
    """Show accounts table, or add/edit/delete account per accounts_command."""
    cmd = getattr(args, "accounts_command", None)
    if cmd == "add":
        return _cmd_accounts_add(args)
    if cmd == "edit":
        return _cmd_accounts_edit(args)
    if cmd == "delete":
        return _cmd_accounts_delete(args)
    # Default: list
    data = load_finances_from_db(args.conn, args.snapshot_id)
    accounts = data.get("accounts") or []
    if not accounts:
        return 0
    include_types = getattr(args, "include_types", None) or []
    exclude_types = getattr(args, "exclude_types", None) or []
    accounts = filter_accounts_by_type(accounts, include_types, exclude_types)

    # Sort if requested
    sort_col = getattr(args, "sort", None)
    sort_dir = getattr(args, "sort_dir", "asc")
    if sort_col is not None:
        accounts = _sort_items(accounts, sort_col, sort_dir == "desc")

    n2 = liquid_minus_cc(accounts)
    account_display_by_id = _account_display_by_id(accounts)
    headers, rows = _build_accounts_table(
        accounts,
        n2,
        show_id=getattr(args, "show_id", False),
        account_display_by_id=account_display_by_id,
    )
    colalign = ("left", "left", "left", "right", "right", "right", "right")
    if getattr(args, "show_id", False):
        colalign = ("right",) + colalign
    print(
        tabulate(
            rows,
            headers=headers,
            tablefmt="simple",
            colalign=colalign,
        )
    )
    return 0


def _cmd_accounts_add(args: argparse.Namespace) -> int:
    """Add an account. Build dict from args (camelCase) and call writer."""
    account = {"name": args.name, "type": args.type}
    if args.type == "credit_card":
        if args.limit is None or args.available is None:
            print(
                "Error: credit_card requires --limit and --available",
                file=sys.stderr,
            )
            return 1
        account["limit"] = args.limit
        account["available"] = args.available
        if getattr(args, "rewards_balance", None) is not None:
            account["rewards_balance"] = args.rewards_balance
        if getattr(args, "statement_balance", None) is not None:
            account["statement_balance"] = args.statement_balance
        if getattr(args, "statement_due_day_of_month", None) is not None:
            account["statement_due_day_of_month"] = args.statement_due_day_of_month
    else:
        account["balance"] = args.balance if args.balance is not None else 0
    for key in (
        "institution",
        "partial_account_number",
        "asOfDate",
        "minimum_balance",
    ):
        val = getattr(args, key, None)
        if val is not None:
            account[key] = val
    try:
        new_id = repo_accounts.add_account(args.conn, args.snapshot_id, account)
        print(f"Added account id {new_id}: {args.name}")
        return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _cmd_accounts_edit(args: argparse.Namespace) -> int:
    """Edit account by id. Only pass non-None args as updates."""
    updates = {}
    for key in (
        "name",
        "type",
        "balance",
        "limit",
        "available",
        "rewards_balance",
        "statement_balance",
        "statement_due_day_of_month",
        "institution",
        "partial_account_number",
        "asOfDate",
        "minimum_balance",
    ):
        val = getattr(args, key, None)
        if val is not None:
            updates[key] = val
    if not updates:
        print("Error: specify at least one field to update", file=sys.stderr)
        return 1
    try:
        repo_accounts.update_account(args.conn, args.snapshot_id, args.id, updates)
        print(f"Updated account id {args.id}")
        return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _cmd_accounts_delete(args: argparse.Namespace) -> int:
    """Delete account by id. --dry-run: show what would be removed."""
    if getattr(args, "dry_run", False):
        data = load_finances_from_db(args.conn, args.snapshot_id)
        accounts = data.get("accounts") or []
        acc = next((a for a in accounts if a.get("id") == args.id), None)
        if not acc:
            print(f"Error: Account id {args.id} not found", file=sys.stderr)
            return 1
        print(f"Would delete account id {args.id}: {acc.get('name', '?')}")
        return 0
    try:
        repo_accounts.delete_account(args.conn, args.snapshot_id, args.id)
        print(f"Deleted account id {args.id}")
        return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _build_income_entry(args: argparse.Namespace) -> dict:
    """Build income budget entry dict from args (camelCase)."""
    entry = {
        "kind": "income",
        "description": args.description,
        "amount": float(args.amount),
        "recurrence": args.recurrence,
    }
    if getattr(args, "type", None):
        entry["type"] = args.type
    if getattr(args, "date", None):
        entry["date"] = args.date
    if getattr(args, "dayOfMonth", None) is not None:
        entry["dayOfMonth"] = args.dayOfMonth
    if getattr(args, "month", None) is not None:
        entry["month"] = args.month
    if getattr(args, "dayOfYear", None) is not None:
        entry["dayOfYear"] = args.dayOfYear
    if getattr(args, "continuous", False):
        entry["continuous"] = True
    if getattr(args, "autoAccountRef", None) is not None:
        entry["autoAccountRef"] = args.autoAccountRef
    return entry


def _build_expense_entry(args: argparse.Namespace) -> dict:
    """Build expense budget entry dict from args (camelCase)."""
    entry = {
        "kind": "expense",
        "description": args.description,
        "amount": float(args.amount),
        "recurrence": args.recurrence,
    }
    if getattr(args, "type", None):
        entry["type"] = args.type
    if getattr(args, "date", None):
        entry["date"] = args.date
    if getattr(args, "dayOfMonth", None) is not None:
        entry["dayOfMonth"] = args.dayOfMonth
    if getattr(args, "month", None) is not None:
        entry["month"] = args.month
    if getattr(args, "dayOfYear", None) is not None:
        entry["dayOfYear"] = args.dayOfYear
    if getattr(args, "continuous", False):
        entry["continuous"] = True
    if getattr(args, "autoAccountRef", None) is not None:
        entry["autoAccountRef"] = args.autoAccountRef
    return entry


def cmd_income(args: argparse.Namespace) -> int:
    """List income or add/edit/delete income entry."""
    cmd = getattr(args, "income_command", None)
    if cmd == "add":
        try:
            repo_budget.add_budget_entry(
                args.conn, args.snapshot_id, _build_income_entry(args)
            )
            print(f"Added income: {args.description}")
            return 0
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    if cmd == "edit":
        updates = {}
        for key in (
            "description",
            "type",
            "amount",
            "recurrence",
            "date",
            "dayOfMonth",
            "month",
            "dayOfYear",
            "continuous",
            "autoAccountRef",
        ):
            val = getattr(args, key, None)
            if val is not None:
                updates[key] = val
        if not updates:
            print("Error: specify at least one field to update", file=sys.stderr)
            return 1
        data = load_finances_from_db(args.conn, args.snapshot_id)
        budget = data.get("budget") or []
        income_entries = [e for e in budget if e.get("kind") == "income"]
        if args.index < 0 or args.index >= len(income_entries):
            print(f"Error: Income index {args.index} out of range", file=sys.stderr)
            return 1
        global_idx = budget.index(income_entries[args.index])
        try:
            repo_budget.update_budget_entry(
                args.conn, args.snapshot_id, global_idx, updates
            )
            print(f"Updated income at index {args.index}")
            return 0
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    if cmd == "delete":
        data = load_finances_from_db(args.conn, args.snapshot_id)
        budget = data.get("budget") or []
        income_entries = [e for e in budget if e.get("kind") == "income"]
        if args.index < 0 or args.index >= len(income_entries):
            print(f"Error: Income index {args.index} out of range", file=sys.stderr)
            return 1
        global_idx = budget.index(income_entries[args.index])
        if getattr(args, "dry_run", False):
            print(
                f"Would delete income at index {args.index}: {income_entries[args.index].get('description', '?')}"
            )
            return 0
        try:
            repo_budget.delete_budget_entry(args.conn, args.snapshot_id, global_idx)
            print(f"Deleted income at index {args.index}")
            return 0
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    # List
    data = load_finances_from_db(args.conn, args.snapshot_id)
    budget = data.get("budget") or []
    income = [e for e in budget if e.get("kind") == "income"]

    # Sort if requested
    sort_col = getattr(args, "sort", None)
    sort_dir = getattr(args, "sort_dir", "asc")
    if sort_col is not None:
        income = _sort_items(income, sort_col, sort_dir == "desc")

    today = date.today()
    headers, rows = _build_budget_table(
        income,
        today.year,
        today.month,
        today.day,
        account_display_by_id=_account_display_by_id(data.get("accounts") or []),
        show_index=getattr(args, "show_id", False),
        annual=getattr(args, "annual", False),
    )
    rows = [
        r
        for r in rows
        if not all(isinstance(c, str) and set(c.strip()) <= {"-"} for c in r)
    ]
    if not rows:
        return 0
    colalign = (
        "left",
        "left",
        "left",
        "right",
        "right",
        "right",
        "left",
        "left",
        "left",
    )
    if getattr(args, "show_id", False):
        colalign = ("right",) + colalign
    print(
        tabulate(
            rows,
            headers=headers,
            tablefmt="simple",
            colalign=colalign,
        )
    )
    return 0


def cmd_expenses(args: argparse.Namespace) -> int:
    """List expenses or add/edit/delete expense entry."""
    cmd = getattr(args, "expenses_command", None)
    if cmd == "add":
        try:
            repo_budget.add_budget_entry(
                args.conn, args.snapshot_id, _build_expense_entry(args)
            )
            print(f"Added expense: {args.description}")
            return 0
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    if cmd == "edit":
        updates = {}
        for key in (
            "description",
            "type",
            "amount",
            "recurrence",
            "date",
            "dayOfMonth",
            "month",
            "dayOfYear",
            "continuous",
            "autoAccountRef",
        ):
            val = getattr(args, key, None)
            if val is not None:
                updates[key] = val
        if not updates:
            print("Error: specify at least one field to update", file=sys.stderr)
            return 1
        data = load_finances_from_db(args.conn, args.snapshot_id)
        budget = data.get("budget") or []
        expense_entries = [e for e in budget if e.get("kind") == "expense"]
        if args.index < 0 or args.index >= len(expense_entries):
            print(f"Error: Expense index {args.index} out of range", file=sys.stderr)
            return 1
        global_idx = budget.index(expense_entries[args.index])
        try:
            repo_budget.update_budget_entry(
                args.conn, args.snapshot_id, global_idx, updates
            )
            print(f"Updated expense at index {args.index}")
            return 0
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    if cmd == "delete":
        data = load_finances_from_db(args.conn, args.snapshot_id)
        budget = data.get("budget") or []
        expense_entries = [e for e in budget if e.get("kind") == "expense"]
        if args.index < 0 or args.index >= len(expense_entries):
            print(f"Error: Expense index {args.index} out of range", file=sys.stderr)
            return 1
        global_idx = budget.index(expense_entries[args.index])
        if getattr(args, "dry_run", False):
            print(
                f"Would delete expense at index {args.index}: {expense_entries[args.index].get('description', '?')}"
            )
            return 0
        try:
            repo_budget.delete_budget_entry(args.conn, args.snapshot_id, global_idx)
            print(f"Deleted expense at index {args.index}")
            return 0
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    # List
    data = load_finances_from_db(args.conn, args.snapshot_id)
    budget = data.get("budget") or []
    expenses = [e for e in budget if e.get("kind") == "expense"]

    # Sort if requested
    sort_col = getattr(args, "sort", None)
    sort_dir = getattr(args, "sort_dir", "asc")
    if sort_col is not None:
        expenses = _sort_items(expenses, sort_col, sort_dir == "desc")

    today = date.today()
    headers, rows = _build_budget_table(
        expenses,
        today.year,
        today.month,
        today.day,
        account_display_by_id=_account_display_by_id(data.get("accounts") or []),
        show_index=getattr(args, "show_id", False),
        annual=getattr(args, "annual", False),
    )
    rows = [
        r
        for r in rows
        if not all(isinstance(c, str) and set(c.strip()) <= {"-"} for c in r)
    ]
    if not rows:
        return 0
    colalign = (
        "left",
        "left",
        "left",
        "right",
        "right",
        "right",
        "left",
        "left",
        "left",
    )
    if getattr(args, "show_id", False):
        colalign = ("right",) + colalign
    print(
        tabulate(
            rows,
            headers=headers,
            tablefmt="simple",
            colalign=colalign,
        )
    )
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    """Show income and expenses (budget) table. Optional filters by kind, type, recurrence."""
    data = load_finances_from_db(args.conn, args.snapshot_id)
    accounts = data.get("accounts") or []
    budget = data.get("budget") or []
    if not budget:
        return 0
    include_kinds = getattr(args, "include_kinds", None) or []
    include_types = getattr(args, "include_types", None) or []
    exclude_types = getattr(args, "exclude_types", None) or []
    include_recurrence = getattr(args, "include_recurrence", None) or []
    exclude_recurrence = getattr(args, "exclude_recurrence", None) or []
    budget = apply_budget_filters(
        budget,
        include_kinds=include_kinds or None,
        include_types=include_types or None,
        exclude_types=exclude_types or None,
        include_recurrence=include_recurrence or None,
        exclude_recurrence=exclude_recurrence or None,
    )

    # Sort if requested
    sort_col = getattr(args, "sort", None)
    sort_dir = getattr(args, "sort_dir", "asc")
    if sort_col is not None:
        budget = _sort_items(budget, sort_col, sort_dir == "desc")

    today = date.today()
    headers, rows = _build_budget_table(
        budget,
        today.year,
        today.month,
        today.day,
        account_display_by_id=_account_display_by_id(accounts),
        show_index=getattr(args, "show_id", False),
        annual=getattr(args, "annual", False),
    )
    colalign = (
        "left",
        "left",
        "left",
        "right",
        "right",
        "right",
        "left",
        "left",
        "left",
    )
    if getattr(args, "show_id", False):
        colalign = ("right",) + colalign
    print(
        tabulate(
            rows,
            headers=headers,
            tablefmt="simple",
            colalign=colalign,
        )
    )
    return 0


def cmd_assets(args: argparse.Namespace) -> int:
    """Show assets and debts table, or add/edit/delete asset."""
    cmd = getattr(args, "assets_command", None)
    if cmd == "add":
        asset = {"kind": "asset", "name": args.name, "value": float(args.value)}
        if getattr(args, "quantity", None) is not None:
            asset["quantity"] = args.quantity
        if getattr(args, "source", None):
            asset["source"] = args.source
        if getattr(args, "institution", None):
            asset["institution"] = args.institution
        try:
            new_id = repo_assets.add_asset_entry(args.conn, args.snapshot_id, asset)
            print(f"Added asset id {new_id}: {args.name}")
            return 0
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    if cmd == "edit":
        updates = {}
        for key in ("name", "value", "quantity", "source", "institution"):
            val = getattr(args, key, None)
            if val is not None:
                updates[key] = float(val) if key in ("value", "quantity") else val
        if not updates:
            print("Error: specify at least one field to update", file=sys.stderr)
            return 1
        data = load_finances_from_db(args.conn, args.snapshot_id)
        all_entries = data.get("assets") or []
        asset_indexed = [
            (gi, e) for gi, e in enumerate(all_entries) if e.get("kind") == "asset"
        ]
        if args.index < 0 or args.index >= len(asset_indexed):
            print(f"Error: Asset index {args.index} out of range", file=sys.stderr)
            return 1
        global_index = asset_indexed[args.index][0]
        try:
            repo_assets.update_asset_entry(
                args.conn, args.snapshot_id, global_index, updates
            )
            print(f"Updated asset at index {args.index}")
            return 0
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    if cmd == "delete":
        data = load_finances_from_db(args.conn, args.snapshot_id)
        all_entries = data.get("assets") or []
        asset_indexed = [
            (gi, e) for gi, e in enumerate(all_entries) if e.get("kind") == "asset"
        ]
        if args.index < 0 or args.index >= len(asset_indexed):
            print(f"Error: Asset index {args.index} out of range", file=sys.stderr)
            return 1
        global_index, entry = asset_indexed[args.index]
        if getattr(args, "dry_run", False):
            print(f"Would delete asset at index {args.index}: {entry.get('name', '?')}")
            return 0
        try:
            repo_assets.delete_asset_entry(args.conn, args.snapshot_id, global_index)
            print(f"Deleted asset at index {args.index}")
            return 0
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    # List
    data = load_finances_from_db(args.conn, args.snapshot_id)
    all_entries = data.get("assets") or []
    if not all_entries:
        return 0
    include_kinds = getattr(args, "include_kinds", None) or []
    filtered = filter_assets_by_kind(all_entries, include_kinds or None)

    # Sort if requested
    sort_col = getattr(args, "sort", None)
    sort_dir = getattr(args, "sort_dir", "asc")
    if sort_col is not None:
        filtered = _sort_items(filtered, sort_col, sort_dir == "desc")

    headers, rows = _build_net_worth_table(
        filtered, show_index=getattr(args, "show_id", False)
    )
    colalign = (
        "left",
        "left",
        "left",
        "right",
        "right",
        "right",
        "left",
        "right",
    )
    if getattr(args, "show_id", False):
        colalign = ("right",) + colalign
    print(
        tabulate(
            rows,
            headers=headers,
            tablefmt="simple",
            colalign=colalign,
        )
    )
    return 0


def cmd_debts(args: argparse.Namespace) -> int:
    """List debts or add/edit/delete debt."""
    cmd = getattr(args, "debts_command", None)
    if cmd == "add":
        entry = {"kind": "debt", "name": args.name, "balance": float(args.balance)}
        if getattr(args, "quantity", None) is not None:
            entry["quantity"] = args.quantity
        if getattr(args, "assetRef", None) is not None:
            entry["assetRef"] = args.assetRef
        if getattr(args, "interestRate", None) is not None:
            entry["interestRate"] = args.interestRate
        if getattr(args, "nextDueDate", None):
            entry["nextDueDate"] = args.nextDueDate
        if getattr(args, "asOfDate", None):
            entry["asOfDate"] = args.asOfDate
        if getattr(args, "institution", None):
            entry["institution"] = args.institution
        try:
            repo_assets.add_asset_entry(args.conn, args.snapshot_id, entry)
            print(f"Added debt: {args.name}")
            return 0
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    if cmd == "edit":
        updates = {}
        for key in (
            "name",
            "balance",
            "quantity",
            "assetRef",
            "interestRate",
            "nextDueDate",
            "asOfDate",
            "institution",
        ):
            val = getattr(args, key, None)
            if val is not None:
                updates[key] = (
                    float(val)
                    if key in ("balance", "quantity", "interestRate")
                    else (int(val) if key == "assetRef" else val)
                )
        if not updates:
            print("Error: specify at least one field to update", file=sys.stderr)
            return 1
        data = load_finances_from_db(args.conn, args.snapshot_id)
        all_entries = data.get("assets") or []
        debt_indexed = [
            (gi, e) for gi, e in enumerate(all_entries) if e.get("kind") == "debt"
        ]
        if args.index < 0 or args.index >= len(debt_indexed):
            print(f"Error: Debt index {args.index} out of range", file=sys.stderr)
            return 1
        global_index = debt_indexed[args.index][0]
        try:
            repo_assets.update_asset_entry(
                args.conn, args.snapshot_id, global_index, updates
            )
            print(f"Updated debt at index {args.index}")
            return 0
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    if cmd == "delete":
        data = load_finances_from_db(args.conn, args.snapshot_id)
        all_entries = data.get("assets") or []
        debt_indexed = [
            (gi, e) for gi, e in enumerate(all_entries) if e.get("kind") == "debt"
        ]
        if args.index < 0 or args.index >= len(debt_indexed):
            print(f"Error: Debt index {args.index} out of range", file=sys.stderr)
            return 1
        global_index, entry = debt_indexed[args.index]
        if getattr(args, "dry_run", False):
            print(f"Would delete debt at index {args.index}: {entry.get('name', '?')}")
            return 0
        try:
            repo_assets.delete_asset_entry(args.conn, args.snapshot_id, global_index)
            print(f"Deleted debt at index {args.index}")
            return 0
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    # List
    data = load_finances_from_db(args.conn, args.snapshot_id)
    all_entries = data.get("assets") or []
    debt_entries = [e for e in all_entries if e.get("kind") == "debt"]
    if not debt_entries:
        return 0

    # Sort if requested
    sort_col = getattr(args, "sort", None)
    sort_dir = getattr(args, "sort_dir", "asc")
    if sort_col is not None:
        debt_entries = _sort_items(debt_entries, sort_col, sort_dir == "desc")

    # Build display list: assets first (for reference lookup), then sorted debts
    asset_entries = [e for e in all_entries if e.get("kind") == "asset"]
    display_entries = asset_entries + debt_entries

    show_id = getattr(args, "show_id", False)
    headers, rows = _build_net_worth_table(display_entries, show_index=show_id)
    # Filter to debt rows only
    index_offset = 1 if show_id else 0
    debt_rows = [
        r for r in rows if len(r) >= (1 + index_offset) and r[index_offset] == "Debt"
    ]
    if not debt_rows:
        return 0
    colalign = ("left", "left", "left", "right", "right", "right", "left", "right")
    if show_id:
        colalign = ("right",) + colalign
    print(
        tabulate(
            debt_rows,
            headers=headers,
            tablefmt="simple",
            colalign=colalign,
        )
    )
    return 0


def cmd_funding(args: argparse.Namespace) -> int:
    """Show funding needed for each liquid account to cover CC statements, direct expenses, and reserve."""
    data = load_finances_from_db(args.conn, args.snapshot_id)
    accounts = data.get("accounts") or []
    budget = data.get("budget") or []
    today = date.today()
    default_reserve = getattr(args, "reserve", 300.0)
    account_id_filter = getattr(args, "account_id", None)

    results = []
    for acc in accounts:
        if _ACCOUNT_TYPE_TO_CALCULATION.get(acc.get("type")) != "liquid":
            continue
        if account_id_filter is not None and acc.get("id") != account_id_filter:
            continue
        results.append(
            account_funding_needed(acc, accounts, budget, today, default_reserve)
        )

    if not results:
        print("No eligible liquid accounts found.")
        return 0

    headers, rows = _build_funding_table(results)
    print(
        tabulate(
            rows,
            headers=headers,
            tablefmt="simple",
            colalign=(
                "left",
                "left",
                "left",
                "right",
                "right",
                "right",
                "right",
                "right",
            ),
        )
    )
    return 0


def main() -> int:
    """Parse args and run the selected command."""
    parser = argparse.ArgumentParser(
        description="Finances tracker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to SQLite database (default: $FINANCES_DB or finances.db)",
    )
    parser.add_argument(
        "snapshot",
        type=str,
        help="Snapshot name to operate on",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prog = parser.prog

    def add_cmd_parser(name: str, help_text: str, epilog: str | None = None):
        p = subparsers.add_parser(
            name,
            help=help_text,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=epilog,
        )
        return p

    status_parser = add_cmd_parser(
        "status",
        "Show current financial status for the data file",
        epilog=f"examples:\n  {prog} finances status\n  {prog} example status",
    )
    status_parser.set_defaults(func=cmd_status)

    accounts_parser = add_cmd_parser(
        "accounts",
        "Show accounts table, or add/edit/delete account (subcommands: add, edit, delete).",
        epilog=f"examples:\n  {prog} finances accounts\n  {prog} finances accounts --sort name\n  {prog} finances accounts --show-id\n  {prog} finances accounts -i checking -i savings\n\nFor subcommand help, use: {prog} finances accounts <subcommand> -h",
    )
    accounts_parser.add_argument(
        "-i",
        "--include",
        action="append",
        dest="include_types",
        default=None,
        metavar="TYPE",
        help="Include only these account types (list only); default is all",
    )
    accounts_parser.add_argument(
        "-x",
        "--exclude",
        action="append",
        dest="exclude_types",
        default=None,
        metavar="TYPE",
        help="Exclude these account types (list only); default is none",
    )
    accounts_parser.add_argument(
        "--sort",
        type=str,
        default=None,
        metavar="KEY",
        help="Sort by field (e.g., name, type, balance)",
    )
    accounts_parser.add_argument(
        "--sort-dir",
        type=str,
        default="asc",
        choices=["asc", "desc"],
        help="Sort direction: asc or desc (default: asc)",
    )
    accounts_parser.add_argument(
        "--show-id",
        action="store_true",
        help="Show ID column (for use with edit/delete commands)",
    )
    accounts_sub = accounts_parser.add_subparsers(dest="accounts_command")
    # accounts add
    add_p = accounts_sub.add_parser(
        "add",
        help="Add an account",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"examples:\n  {prog} finances accounts add --name Savings --type savings --balance 500\n  {prog} finances accounts add --name 'Chase Freedom' --type credit_card --limit 5000 --available 4800",
    )
    add_p.add_argument("--name", required=True, help="Account name")
    add_p.add_argument(
        "--type",
        required=True,
        choices=[
            "credit_card",
            "credit_card_rewards",
            "checking",
            "savings",
            "gift_card",
            "wallet",
            "digital_wallet",
            "loan",
            "other",
        ],
        help="Account type",
    )
    add_p.add_argument(
        "--balance", type=float, default=None, help="Balance (non-credit_card)"
    )
    add_p.add_argument(
        "--limit", type=float, default=None, help="Credit limit (credit_card)"
    )
    add_p.add_argument(
        "--available", type=float, default=None, help="Available credit (credit_card)"
    )
    add_p.add_argument("--rewards_balance", type=float, default=None)
    add_p.add_argument("--statement_balance", type=float, default=None)
    add_p.add_argument("--statement_due_day_of_month", type=int, default=None)
    add_p.add_argument("--institution", type=str, default=None)
    add_p.add_argument("--partial_account_number", type=str, default=None)
    add_p.add_argument("--asOfDate", type=str, default=None)
    add_p.add_argument("--minimum_balance", type=float, default=None)
    # accounts edit <id>
    edit_p = accounts_sub.add_parser(
        "edit",
        help="Edit account by id",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"examples:\n  {prog} finances accounts edit 1 --name 'Main Checking'\n  {prog} finances accounts edit 2 --balance 1250.50\n  {prog} finances accounts edit 3 --available 4500",
    )
    edit_p.add_argument("id", type=int, help="Account id")
    edit_p.add_argument("--name", type=str, default=None)
    edit_p.add_argument(
        "--type",
        type=str,
        default=None,
        choices=[
            "credit_card",
            "credit_card_rewards",
            "checking",
            "savings",
            "gift_card",
            "wallet",
            "digital_wallet",
            "loan",
            "other",
        ],
    )
    edit_p.add_argument("--balance", type=float, default=None)
    edit_p.add_argument("--limit", type=float, default=None)
    edit_p.add_argument("--available", type=float, default=None)
    edit_p.add_argument("--rewards_balance", type=float, default=None)
    edit_p.add_argument("--statement_balance", type=float, default=None)
    edit_p.add_argument("--statement_due_day_of_month", type=int, default=None)
    edit_p.add_argument("--institution", type=str, default=None)
    edit_p.add_argument("--partial_account_number", type=str, default=None)
    edit_p.add_argument("--asOfDate", type=str, default=None)
    edit_p.add_argument("--minimum_balance", type=float, default=None)
    # accounts delete <id>
    del_p = accounts_sub.add_parser(
        "delete",
        help="Delete account by id",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"examples:\n  {prog} finances accounts delete 2 --dry-run\n  {prog} finances accounts delete 2",
    )
    del_p.add_argument("id", type=int, help="Account id")
    del_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without writing",
    )
    del_p.add_argument("--force", action="store_true", help="Delete without prompt")
    accounts_parser.set_defaults(func=cmd_accounts)

    budget_parser = add_cmd_parser(
        "budget",
        "Show income and expenses (budget) table with prorated subtotals and budget amounts. Optional filters by kind, type, recurrence.",
        epilog=f"examples:\n  {prog} finances budget\n  {prog} finances budget --annual\n  {prog} finances budget --sort amount --sort-dir desc\n  {prog} finances budget --show-id\n  {prog} finances budget --kind income -i salary\n  {prog} finances budget -x insurance --exclude-recurrence one_time",
    )
    budget_parser.add_argument(
        "--annual",
        action="store_true",
        help="Show annual budget amounts instead of monthly",
    )
    budget_parser.add_argument(
        "--kind",
        action="append",
        dest="include_kinds",
        default=None,
        metavar="KIND",
        help="Include only this kind: income or expense (can repeat); default is both",
    )
    budget_parser.add_argument(
        "-i",
        "--include",
        action="append",
        dest="include_types",
        default=None,
        metavar="TYPE",
        help="Include only these income/expense types (can repeat); default is all",
    )
    budget_parser.add_argument(
        "-x",
        "--exclude",
        action="append",
        dest="exclude_types",
        default=None,
        metavar="TYPE",
        help="Exclude these income/expense types (can repeat); default is none",
    )
    budget_parser.add_argument(
        "--include-recurrence",
        action="append",
        dest="include_recurrence",
        default=None,
        metavar="RECURRENCE",
        help="Include only these recurrences (can repeat); default is all",
    )
    budget_parser.add_argument(
        "--exclude-recurrence",
        action="append",
        dest="exclude_recurrence",
        default=None,
        metavar="RECURRENCE",
        help="Exclude these recurrences (can repeat); default is none",
    )
    budget_parser.add_argument(
        "--sort",
        type=str,
        default=None,
        metavar="KEY",
        help="Sort by field (e.g., description, amount, type)",
    )
    budget_parser.add_argument(
        "--sort-dir",
        type=str,
        default="asc",
        choices=["asc", "desc"],
        help="Sort direction: asc or desc (default: asc)",
    )
    budget_parser.add_argument(
        "--show-id",
        action="store_true",
        help="Show index column (for use with income/expenses edit/delete commands)",
    )
    budget_parser.set_defaults(func=cmd_budget)

    # income add/edit/delete
    income_parser = add_cmd_parser(
        "income",
        "List income or add/edit/delete income entry (subcommands: add, edit, delete).",
        epilog=f"examples:\n  {prog} finances income\n  {prog} finances income --annual\n  {prog} finances income --sort amount --sort-dir desc\n  {prog} finances income --show-id\n\nFor subcommand help, use: {prog} finances income <subcommand> -h",
    )
    income_parser.add_argument(
        "--annual",
        action="store_true",
        help="Show annual budget amounts instead of monthly",
    )
    income_sub = income_parser.add_subparsers(dest="income_command")
    income_add = income_sub.add_parser(
        "add",
        help="Add income entry",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"examples:\n  {prog} finances income add --description Salary --amount 5000 --recurrence monthly --dayOfMonth 1\n  {prog} finances income add --description Bonus --amount 1000 --recurrence one_time --date 2026-12-15",
    )
    income_add.add_argument("--description", required=True)
    income_add.add_argument("--amount", type=float, required=True)
    income_add.add_argument(
        "--recurrence",
        required=True,
        choices=[
            "one_time",
            "monthly",
            "biweekly",
            "quarterly",
            "semiannual",
            "annual",
        ],
    )
    income_add.add_argument(
        "--type", choices=["salary", "refund", "bonus", "remittance"], default=None
    )
    income_add.add_argument("--date", default=None)
    income_add.add_argument("--dayOfMonth", type=int, default=None)
    income_add.add_argument("--month", type=int, default=None)
    income_add.add_argument("--dayOfYear", type=int, default=None)
    income_add.add_argument("--continuous", action="store_true")
    income_add.add_argument("--autoAccountRef", type=int, default=None)
    income_edit = income_sub.add_parser(
        "edit",
        help="Edit income at index",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"examples:\n  {prog} finances income edit 0 --amount 6000\n  {prog} finances income edit 1 --description 'Updated salary'",
    )
    income_edit.add_argument("index", type=int)
    income_edit.add_argument("--description", default=None)
    income_edit.add_argument("--amount", type=float, default=None)
    income_edit.add_argument("--recurrence", default=None)
    income_edit.add_argument("--type", default=None)
    income_edit.add_argument("--date", default=None)
    income_edit.add_argument("--dayOfMonth", type=int, default=None)
    income_edit.add_argument("--month", type=int, default=None)
    income_edit.add_argument("--dayOfYear", type=int, default=None)
    income_edit.add_argument("--continuous", action="store_true", default=None)
    income_edit.add_argument("--autoAccountRef", type=int, default=None)
    income_del = income_sub.add_parser(
        "delete",
        help="Delete income at index",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"examples:\n  {prog} finances income delete 0 --dry-run\n  {prog} finances income delete 0",
    )
    income_del.add_argument("index", type=int)
    income_del.add_argument("--dry-run", action="store_true")
    income_parser.add_argument(
        "--sort",
        type=str,
        default=None,
        metavar="KEY",
        help="Sort by field (e.g., description, amount, type)",
    )
    income_parser.add_argument(
        "--sort-dir",
        type=str,
        default="asc",
        choices=["asc", "desc"],
        help="Sort direction: asc or desc (default: asc)",
    )
    income_parser.add_argument(
        "--show-id",
        action="store_true",
        help="Show index column (for use with edit/delete commands)",
    )
    income_parser.set_defaults(func=cmd_income)

    # expenses add/edit/delete
    expenses_parser = add_cmd_parser(
        "expenses",
        "List expenses or add/edit/delete expense entry (subcommands: add, edit, delete).",
        epilog=f"examples:\n  {prog} finances expenses\n  {prog} finances expenses --annual\n  {prog} finances expenses --sort amount --sort-dir desc\n  {prog} finances expenses --show-id\n\nFor subcommand help, use: {prog} finances expenses <subcommand> -h",
    )
    expenses_parser.add_argument(
        "--annual",
        action="store_true",
        help="Show annual budget amounts instead of monthly",
    )
    expenses_sub = expenses_parser.add_subparsers(dest="expenses_command")
    exp_add = expenses_sub.add_parser(
        "add",
        help="Add expense entry",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"examples:\n  {prog} finances expenses add --description Rent --amount 1500 --recurrence monthly --dayOfMonth 1\n  {prog} finances expenses add --description 'Car repair' --amount 350 --recurrence one_time --date 2026-02-10",
    )
    exp_add.add_argument("--description", required=True)
    exp_add.add_argument("--amount", type=float, required=True)
    exp_add.add_argument(
        "--recurrence",
        required=True,
        choices=[
            "one_time",
            "monthly",
            "biweekly",
            "quarterly",
            "semiannual",
            "annual",
        ],
    )
    exp_add.add_argument(
        "--type",
        choices=[
            "housing",
            "insurance",
            "service",
            "utility",
            "product",
            "transport",
            "food",
        ],
        default=None,
    )
    exp_add.add_argument("--date", default=None)
    exp_add.add_argument("--dayOfMonth", type=int, default=None)
    exp_add.add_argument("--month", type=int, default=None)
    exp_add.add_argument("--dayOfYear", type=int, default=None)
    exp_add.add_argument("--continuous", action="store_true")
    exp_add.add_argument("--autoAccountRef", type=int, default=None)
    exp_edit = expenses_sub.add_parser(
        "edit",
        help="Edit expense at index",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"examples:\n  {prog} finances expenses edit 0 --amount 1600\n  {prog} finances expenses edit 2 --description 'Updated rent'",
    )
    exp_edit.add_argument("index", type=int)
    exp_edit.add_argument("--description", default=None)
    exp_edit.add_argument("--amount", type=float, default=None)
    exp_edit.add_argument("--recurrence", default=None)
    exp_edit.add_argument("--type", default=None)
    exp_edit.add_argument("--date", default=None)
    exp_edit.add_argument("--dayOfMonth", type=int, default=None)
    exp_edit.add_argument("--month", type=int, default=None)
    exp_edit.add_argument("--dayOfYear", type=int, default=None)
    exp_edit.add_argument("--continuous", action="store_true", default=None)
    exp_edit.add_argument("--autoAccountRef", type=int, default=None)
    exp_del = expenses_sub.add_parser(
        "delete",
        help="Delete expense at index",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"examples:\n  {prog} finances expenses delete 0 --dry-run\n  {prog} finances expenses delete 0",
    )
    exp_del.add_argument("index", type=int)
    exp_del.add_argument("--dry-run", action="store_true")
    expenses_parser.add_argument(
        "--sort",
        type=str,
        default=None,
        metavar="KEY",
        help="Sort by field (e.g., description, amount, type)",
    )
    expenses_parser.add_argument(
        "--sort-dir",
        type=str,
        default="asc",
        choices=["asc", "desc"],
        help="Sort direction: asc or desc (default: asc)",
    )
    expenses_parser.add_argument(
        "--show-id",
        action="store_true",
        help="Show index column (for use with edit/delete commands)",
    )
    expenses_parser.set_defaults(func=cmd_expenses)

    assets_parser = add_cmd_parser(
        "assets",
        "Show assets and debts table, or add/edit/delete asset (subcommands: add, edit, delete).",
        epilog=f"examples:\n  {prog} finances assets\n  {prog} finances assets --sort value --sort-dir desc\n  {prog} finances assets --show-id\n  {prog} finances assets --kind asset\n\nFor subcommand help, use: {prog} finances assets <subcommand> -h",
    )
    assets_parser.add_argument(
        "--kind",
        action="append",
        dest="include_kinds",
        default=None,
        metavar="KIND",
        help="Include only this kind: asset or debt (list only); default is both",
    )
    assets_parser.add_argument(
        "--sort",
        type=str,
        default=None,
        metavar="KEY",
        help="Sort by field (e.g., name, value, institution)",
    )
    assets_parser.add_argument(
        "--sort-dir",
        type=str,
        default="asc",
        choices=["asc", "desc"],
        help="Sort direction: asc or desc (default: asc)",
    )
    assets_parser.add_argument(
        "--show-id",
        action="store_true",
        help="Show index column (for use with edit/delete commands)",
    )
    assets_sub = assets_parser.add_subparsers(dest="assets_command")
    ast_add = assets_sub.add_parser(
        "add",
        help="Add asset",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"examples:\n  {prog} finances assets add --name Car --value 25000\n  {prog} finances assets add --name 'AAPL Stock' --value 150 --quantity 10",
    )
    ast_add.add_argument("--name", required=True)
    ast_add.add_argument("--value", type=float, required=True)
    ast_add.add_argument("--quantity", type=float, default=None)
    ast_add.add_argument("--source", default=None)
    ast_add.add_argument("--institution", default=None)
    ast_edit = assets_sub.add_parser(
        "edit",
        help="Edit asset at index",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"examples:\n  {prog} finances assets edit 0 --value 24000\n  {prog} finances assets edit 1 --quantity 12",
    )
    ast_edit.add_argument("index", type=int)
    ast_edit.add_argument("--name", default=None)
    ast_edit.add_argument("--value", type=float, default=None)
    ast_edit.add_argument("--quantity", type=float, default=None)
    ast_edit.add_argument("--source", default=None)
    ast_edit.add_argument("--institution", default=None)
    ast_del = assets_sub.add_parser(
        "delete",
        help="Delete asset at index",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"examples:\n  {prog} finances assets delete 1 --dry-run\n  {prog} finances assets delete 1",
    )
    ast_del.add_argument("index", type=int)
    ast_del.add_argument("--dry-run", action="store_true")
    assets_parser.set_defaults(func=cmd_assets)

    debts_parser = add_cmd_parser(
        "debts",
        "List debts or add/edit/delete debt (subcommands: add, edit, delete).",
        epilog=f"examples:\n  {prog} finances debts\n  {prog} finances debts --sort balance --sort-dir desc\n  {prog} finances debts --show-id\n\nFor subcommand help, use: {prog} finances debts <subcommand> -h",
    )
    debts_parser.add_argument(
        "--sort",
        type=str,
        default=None,
        metavar="KEY",
        help="Sort by field (e.g., name, balance)",
    )
    debts_parser.add_argument(
        "--sort-dir",
        type=str,
        default="asc",
        choices=["asc", "desc"],
        help="Sort direction: asc or desc (default: asc)",
    )
    debts_parser.add_argument(
        "--show-id",
        action="store_true",
        help="Show index column (for use with edit/delete commands)",
    )
    debts_sub = debts_parser.add_subparsers(dest="debts_command")
    dbt_add = debts_sub.add_parser(
        "add",
        help="Add debt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"examples:\n  {prog} finances debts add --name 'Car loan' --balance 15000\n  {prog} finances debts add --name Mortgage --balance 250000 --assetRef 0 --interestRate 0.035",
    )
    dbt_add.add_argument("--name", required=True)
    dbt_add.add_argument("--balance", type=float, required=True)
    dbt_add.add_argument("--quantity", type=float, default=None)
    dbt_add.add_argument("--assetRef", type=int, default=None)
    dbt_add.add_argument("--interestRate", type=float, default=None)
    dbt_add.add_argument("--nextDueDate", default=None)
    dbt_add.add_argument("--asOfDate", default=None)
    dbt_add.add_argument("--institution", default=None)
    dbt_edit = debts_sub.add_parser(
        "edit",
        help="Edit debt at index",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"examples:\n  {prog} finances debts edit 0 --balance 14000\n  {prog} finances debts edit 1 --interestRate 0.04",
    )
    dbt_edit.add_argument("index", type=int)
    dbt_edit.add_argument("--name", default=None)
    dbt_edit.add_argument("--balance", type=float, default=None)
    dbt_edit.add_argument("--quantity", type=float, default=None)
    dbt_edit.add_argument("--assetRef", type=int, default=None)
    dbt_edit.add_argument("--interestRate", type=float, default=None)
    dbt_edit.add_argument("--nextDueDate", default=None)
    dbt_edit.add_argument("--asOfDate", default=None)
    dbt_edit.add_argument("--institution", default=None)
    dbt_del = debts_sub.add_parser(
        "delete",
        help="Delete debt at index",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"examples:\n  {prog} finances debts delete 0 --dry-run\n  {prog} finances debts delete 0",
    )
    dbt_del.add_argument("index", type=int)
    dbt_del.add_argument("--dry-run", action="store_true")
    debts_parser.set_defaults(func=cmd_debts)

    funding_parser = add_cmd_parser(
        "funding",
        "Show funding needed for each liquid account (covers CC autopay, direct expenses, reserve).",
        epilog=f"examples:\n  {prog} finances funding\n  {prog} finances funding --reserve 500\n  {prog} finances funding --account-id 1",
    )
    funding_parser.add_argument(
        "--reserve",
        type=float,
        default=300.0,
        help="Default reserve floor for accounts without minimum_balance (default: 300)",
    )
    funding_parser.add_argument(
        "--account-id",
        type=int,
        default=None,
        dest="account_id",
        help="Filter to a single account by id",
    )
    funding_parser.set_defaults(func=cmd_funding)

    args = parser.parse_args()

    db_path = args.db or os.environ.get("FINANCES_DB") or "finances.db"
    engine = get_engine(db_path)
    init_db(engine)

    with engine.connect() as conn:
        snapshot_id = get_snapshot_id(conn, args.snapshot)
        if snapshot_id is None:
            names = list_snapshots(conn)
            if names:
                print(
                    f"Error: Snapshot '{args.snapshot}' not found. Available: {', '.join(names)}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"Error: Snapshot '{args.snapshot}' not found. Database is empty — run yaml_import first.",
                    file=sys.stderr,
                )
            return 1
        args.conn = conn
        args.snapshot_id = snapshot_id
        return args.func(args)
