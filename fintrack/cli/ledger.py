"""Ledger command groups: import, staging, duplicates, merchants, categories,
report, balance, serve (ported from the spending CLI plus new balance/staging/report
commands)."""

from datetime import date, datetime
from pathlib import Path

import click

from fintrack.accounts.balance_history import get_balance_history, record_balance
from fintrack.cli.helpers import echo_table, pass_cli
from fintrack.core.coerce import to_date
from fintrack.core.formatting import fmt_money
from fintrack.ledger.classifier import classify_and_cache
from fintrack.ledger.duplicate_scan import scan_for_duplicates
from fintrack.ledger.importer import run_import
from fintrack.ledger.repository.accounts import get_account_by_name
from fintrack.ledger.repository.aggregations import (
    get_monthly_category_totals,
    get_monthly_totals_range,
)
from fintrack.ledger.repository.categories import (
    add_category,
    delete_category,
    edit_category,
    list_categories,
)
from fintrack.ledger.repository.duplicates import (
    DUPLICATE_STATUSES,
    list_duplicates,
    resolve_duplicate,
)
from fintrack.ledger.repository.imports import (
    confirm_import,
    find_duplicate_transactions,
    get_staging_imports,
    reject_import,
    remove_duplicate_transactions,
)
from fintrack.ledger.repository.merchants import (
    list_merchants_with_stats,
    set_merchant_category,
)


def _resolve_account(cli, conn, name: str) -> dict:
    snapshot_id = cli.snapshot_id(conn)
    account = get_account_by_name(conn, name, snapshot_id)
    if not account:
        raise click.ClickException(f"Account not found in snapshot: {name}")
    return account


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


@click.command("import")
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--account", required=True, help="Account name to import into")
@pass_cli
def import_cmd(cli, files, account):
    """Import statement files (OFX/QFX/CSV) into the staging area."""
    file_paths = []
    for f in files:
        p = Path(f)
        if p.is_dir():
            file_paths.extend(p.glob("*.ofx"))
            file_paths.extend(p.glob("*.qfx"))
            file_paths.extend(p.glob("*.csv"))
        else:
            file_paths.append(p)
    if not file_paths:
        click.echo("No supported files found.")
        return

    with cli.connect() as conn:
        account_id = _resolve_account(cli, conn, account)["id"]

        all_new_merchants = set()
        for fp in file_paths:
            click.echo(f"Importing {fp.name}...")
            result = run_import(conn, fp, account_id)
            if result.get("error"):
                click.echo(f"  Error: {result['error']}")
                continue
            click.echo(
                f"  {result['new_count']} new, "
                f"{result['skipped_count']} skipped, "
                f"{result['flagged_count']} flagged"
            )
            all_new_merchants.update(result["new_merchants"])

        if all_new_merchants:
            classified, warn = classify_and_cache(conn, list(all_new_merchants))
            if warn:
                click.echo(f"Warning: {warn}", err=True)
            elif classified:
                click.echo(f"Classified {classified} new merchants.")

        click.echo("Done. Review with `fintrack staging list` or in the web UI.")


# ---------------------------------------------------------------------------
# staging
# ---------------------------------------------------------------------------


@click.group()
def staging():
    """Review pending imports."""


@staging.command("list")
@pass_cli
def staging_list(cli):
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        pending = get_staging_imports(conn, snapshot_id)
    if not pending:
        click.echo("No pending imports.")
        return
    rows = [
        [
            i["id"],
            i["filename"],
            i["txn_count"],
            i["imported_at"],
            fmt_money(i["ledger_balance"]) if i["ledger_balance"] is not None else "-",
        ]
        for i in pending
    ]
    echo_table(
        rows,
        ["ID", "File", "Txns", "Imported", "Stmt balance"],
        ("right", "left", "right", "left", "right"),
    )


@staging.command("confirm")
@click.argument("import_id", type=int)
@pass_cli
def staging_confirm(cli, import_id):
    """Confirm a staged import (its statement balance lands in history)."""
    with cli.connect() as conn:
        confirm_import(conn, import_id)
    click.echo(f"Confirmed import {import_id}")


@staging.command("reject")
@click.argument("import_id", type=int)
@pass_cli
def staging_reject(cli, import_id):
    """Reject a staged import and delete its transactions."""
    with cli.connect() as conn:
        reject_import(conn, import_id)
    click.echo(f"Rejected import {import_id}")


# ---------------------------------------------------------------------------
# dedup
# ---------------------------------------------------------------------------


@click.command("dedup")
@click.option(
    "--apply", "apply_", is_flag=True, help="Remove duplicates (default: dry run)"
)
@pass_cli
def dedup(cli, apply_):
    """Report (or with --apply, remove) transactions that share a fingerprint.

    Duplicate detection normally prevents these at import time; use this to
    clean up copies left by an earlier bug. Removal keeps one row per
    fingerprint (preferring one carrying a correction), mirroring import-time
    dedup. Run `alembic upgrade head` first so fingerprints are current.
    """
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        groups = find_duplicate_transactions(conn, snapshot_id)
        if not groups:
            click.echo("No duplicate transactions found.")
            return

        removable = sum(g["copies"] - 1 for g in groups)
        rows = [
            [
                g["date"],
                g["merchant"],
                fmt_money(g["amount"]),
                g["copies"],
                g["copies"] - 1,
            ]
            for g in groups
        ]
        echo_table(
            rows,
            ["Date", "Merchant", "Amount", "Copies", "Removable"],
            ("left", "left", "right", "right", "right"),
        )
        click.echo(
            f"\n{len(groups)} duplicated transaction(s); {removable} row(s) removable."
        )

        if not apply_:
            click.echo("Dry run — re-run with --apply to remove.")
            return

        removed = remove_duplicate_transactions(conn, snapshot_id)
        click.echo(f"Removed {removed} duplicate transaction(s).")


# ---------------------------------------------------------------------------
# duplicates
# ---------------------------------------------------------------------------


@click.group()
def duplicates():
    """Review possible duplicate transactions (near-matches across imports).

    A flag pairs a transaction with an earlier one it resembles: same amount,
    dates within 3 days, similar merchant. Marking it a duplicate hides it from
    every report; dismissing keeps it. Exact copies are handled by `dedup`.
    """


@duplicates.command("list")
@click.option(
    "--all", "show_all", is_flag=True, help="Include resolved flags (default: open)"
)
@pass_cli
def duplicates_list(cli, show_all):
    """List open possible-duplicate flags (or all with --all)."""
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        flags = list_duplicates(
            conn,
            snapshot_id,
            statuses=DUPLICATE_STATUSES if show_all else ("flagged",),
        )
    if not flags:
        click.echo("No possible duplicates." if not show_all else "No flags.")
        return
    rows = [
        [
            f["transaction_id"],
            f["date"],
            f["raw_description"],
            fmt_money(f["amount"]),
            f["duplicate_of_id"],
            f["of_date"],
            f["of_raw_description"],
            f["reason"],
        ]
        + ([f["status"]] if show_all else [])
        for f in flags
    ]
    headers = ["ID", "Date", "Description", "Amount", "Of ID", "Of date"]
    headers += ["Of description", "Why"] + (["Status"] if show_all else [])
    echo_table(
        rows,
        headers,
        ("right", "left", "left", "right", "right", "left", "left", "left")
        + (("left",) if show_all else ()),
    )
    if not show_all:
        click.echo(
            "\nResolve with `fintrack duplicates mark ID` (hide it) or "
            "`fintrack duplicates dismiss ID` (keep it)."
        )


@duplicates.command("scan")
@click.option(
    "--apply", "apply_", is_flag=True, help="Record the flags (default: dry run)"
)
@pass_cli
def duplicates_scan(cli, apply_):
    """Find possible duplicates among already-confirmed transactions.

    Replays import-time matching over existing data, so copies imported before
    duplicate flagging existed get flagged for review. Rows that already carry
    a flag (including ones dismissed as not duplicates) are left alone.
    """
    with cli.connect() as conn:
        found = scan_for_duplicates(conn, cli.snapshot_id(conn), apply=apply_)
    if not found:
        click.echo("No new possible duplicates found.")
        return
    rows = [
        [
            f["txn"]["id"],
            f["txn"]["date"],
            f["txn"]["raw_description"],
            fmt_money(f["txn"]["amount"]),
            f["of"]["id"],
            f["of"]["date"],
            f["of"]["raw_description"],
            f["reason"],
        ]
        for f in found
    ]
    echo_table(
        rows,
        ["ID", "Date", "Description", "Amount", "Of ID", "Of date"]
        + ["Of description", "Why"],
        ("right", "left", "left", "right", "right", "left", "left", "left"),
    )
    click.echo(f"\n{len(found)} possible duplicate(s).")
    if apply_:
        click.echo("Flagged for review — see `fintrack duplicates list`.")
    else:
        click.echo("Dry run — re-run with --apply to flag them for review.")


def _resolve(cli, transaction_id: int, status: str, message: str) -> None:
    with cli.connect() as conn:
        try:
            resolve_duplicate(
                conn, transaction_id, status, snapshot_id=cli.snapshot_id(conn)
            )
        except LookupError as exc:
            raise click.ClickException(str(exc)) from exc
    click.echo(message.format(id=transaction_id))


@duplicates.command("mark")
@click.argument("transaction_id", type=int)
@pass_cli
def duplicates_mark(cli, transaction_id):
    """Mark a flagged transaction as a duplicate (hidden everywhere)."""
    _resolve(cli, transaction_id, "duplicate", "Marked transaction {id} a duplicate.")


@duplicates.command("dismiss")
@click.argument("transaction_id", type=int)
@pass_cli
def duplicates_dismiss(cli, transaction_id):
    """Keep a flagged transaction: it is not a duplicate."""
    _resolve(cli, transaction_id, "not_duplicate", "Kept transaction {id}.")


@duplicates.command("undo")
@click.argument("transaction_id", type=int)
@pass_cli
def duplicates_undo(cli, transaction_id):
    """Reopen a resolved flag."""
    _resolve(cli, transaction_id, "flagged", "Reopened the flag on transaction {id}.")


# ---------------------------------------------------------------------------
# balance
# ---------------------------------------------------------------------------


@click.group()
def balance():
    """Record and inspect account balance history."""


@balance.command("set")
@click.argument("account_name")
@click.argument("amount", type=float)
@click.option("--date", "as_of", default=None, help="Backdate (ISO, default today)")
@pass_cli
def balance_set(cli, account_name, amount, as_of):
    """Record a manual balance point for an account."""
    with cli.connect() as conn:
        account = _resolve_account(cli, conn, account_name)
        record_balance(
            conn,
            account_id=account["id"],
            balance=amount,
            as_of=to_date(as_of),
            source="manual",
        )
    click.echo(f"Recorded balance {fmt_money(amount)} for {account_name}")


@balance.command("history")
@click.argument("account_name")
@click.option("--limit", type=int, default=None)
@pass_cli
def balance_history_cmd(cli, account_name, limit):
    """Show the balance history for an account, oldest first."""
    with cli.connect() as conn:
        account = _resolve_account(cli, conn, account_name)
        points = get_balance_history(conn, account["id"], limit=limit)
    if not points:
        click.echo("No balance history.")
        return
    rows = [
        [p["as_of"], fmt_money(p["balance"]), p["source"], p.get("note") or ""]
        for p in points
    ]
    echo_table(
        rows, ["As of", "Balance", "Source", "Note"], ("left", "right", "left", "left")
    )


# ---------------------------------------------------------------------------
# merchants / categories
# ---------------------------------------------------------------------------


@click.group()
def merchants():
    """Merchant classification cache."""


@merchants.command("list")
@click.option("--search", default=None)
@pass_cli
def merchants_list(cli, search):
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        rows_data = list_merchants_with_stats(conn, snapshot_id=snapshot_id)
    if search:
        rows_data = [
            m for m in rows_data if search.upper() in m["merchant_name"].upper()
        ]
    rows = [
        [m["merchant_name"], m["category"], m["source"], m["txn_count"], m["last_seen"]]
        for m in sorted(rows_data, key=lambda m: m["merchant_name"].lower())
    ]
    echo_table(
        rows,
        ["Merchant", "Category", "Source", "Txns", "Last seen"],
        ("left", "left", "left", "right", "left"),
    )


@merchants.command("set")
@click.argument("merchant_name")
@click.argument("category")
@pass_cli
def merchants_set(cli, merchant_name, category):
    """Set a merchant's category (source=manual)."""
    with cli.connect() as conn:
        set_merchant_category(conn, merchant_name, category, source="manual")
    click.echo(f"Set {merchant_name} -> {category}")


@click.group()
def categories():
    """Manage the category taxonomy."""


@categories.command("list")
@pass_cli
def categories_list(cli):
    with cli.connect() as conn:
        cats = list_categories(conn)
    for c in cats:
        click.echo(f"  [{c['id']}] {c['name']} (order: {c['sort_order']})")


@categories.command("add")
@click.option("--name", required=True)
@click.option(
    "--sort-order",
    type=int,
    default=None,
    help="Defaults to appending after the current highest sort order.",
)
@pass_cli
def categories_add(cli, name, sort_order):
    with cli.connect() as conn:
        try:
            add_category(conn, name=name, sort_order=sort_order)
        except ValueError as e:
            raise click.ClickException(str(e)) from e
    click.echo(f"Added category: {name}")


@categories.command("edit")
@click.argument("category_id", type=int)
@click.option("--name")
@click.option("--sort-order", type=int)
@pass_cli
def categories_edit(cli, category_id, name, sort_order):
    with cli.connect() as conn:
        try:
            edit_category(conn, category_id, name=name, sort_order=sort_order)
        except ValueError as e:
            raise click.ClickException(str(e)) from e
    click.echo(f"Updated category {category_id}")


@categories.command("delete")
@click.argument("name")
@pass_cli
def categories_delete(cli, name):
    with cli.connect() as conn:
        try:
            delete_category(conn, name=name)
        except ValueError as e:
            raise click.ClickException(str(e)) from e
    click.echo(f"Deleted category: {name}")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


@click.group()
def report():
    """Spending reports from the confirmed ledger."""


@report.command("monthly")
@click.option("--year", type=int, default=None)
@click.option("--month", type=int, default=None)
@pass_cli
def report_monthly(cli, year, month):
    """Category totals for one month (default: current)."""
    today = datetime.now().astimezone().date()
    year, month = year or today.year, month or today.month
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        totals = get_monthly_category_totals(
            conn, year=year, month=month, snapshot_id=snapshot_id
        )
        pending = get_staging_imports(conn, snapshot_id)
    if not totals:
        click.echo(f"No spending data for {date(year, month, 1).strftime('%B %Y')}.")
    else:
        rows = [[t["category"], fmt_money(t["total"]), t["count"]] for t in totals]
        grand = sum(t["total"] for t in totals)
        rows.append(["Total", fmt_money(grand), sum(t["count"] for t in totals)])
        click.echo(date(year, month, 1).strftime("%B %Y"))
        echo_table(rows, ["Category", "Total", "Txns"], ("left", "right", "right"))
    if pending:
        click.echo(f"\n{len(pending)} pending import(s) awaiting review.")


@report.command("trends")
@click.option("--months", type=int, default=12, show_default=True)
@pass_cli
def report_trends(cli, months):
    """Per-category totals and monthly average over the trailing N months."""
    today = datetime.now().astimezone().date()
    start_month = today.month - (months - 1)
    start_year = today.year
    while start_month <= 0:
        start_month += 12
        start_year -= 1
    start = date(start_year, start_month, 1)
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        monthly = get_monthly_totals_range(
            conn, start_date=start, end_date=today, snapshot_id=snapshot_id
        )
    if not monthly:
        click.echo("No spending data in range.")
        return
    by_category: dict = {}
    for row in monthly:
        by_category.setdefault(row["category"], []).append(row["total"])
    rows = [
        [cat, fmt_money(sum(vals)), fmt_money(sum(vals) / months)]
        for cat, vals in sorted(by_category.items(), key=lambda kv: sum(kv[1]))
    ]
    click.echo(f"Trailing {months} months ({start} – {today})")
    echo_table(rows, ["Category", "Total", "Avg/month"], ("left", "right", "right"))


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@click.command()
@click.option("--port", default=5003, type=int, show_default=True)
@pass_cli
def serve(cli, port):
    """Start the web server against this database."""
    from web.app import create_app

    app = create_app(db_path=cli.db_path)
    app.run(debug=True, port=port)
