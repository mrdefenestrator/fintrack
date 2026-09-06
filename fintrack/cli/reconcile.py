"""Transaction<->budget-entry association commands (issue #53): link, unlink,
suggest, and per-entry budget-vs-actual. Mirrors the web Holdings/Budget
parity requirement — every insight the web surfaces is reachable from the CLI.
"""

from datetime import datetime

import click

from fintrack.budget.reconcile import (
    SnapshotMismatch,
    budget_actuals,
    link_transaction,
    suggest_links,
    unlink_transaction,
)
from fintrack.budget.repository import get_budget_entries
from fintrack.cli.helpers import echo_table, pass_cli
from fintrack.core.formatting import fmt_money

_STATUS_LABEL = {
    "matched": "✓ matched",
    "over": "▲ over",
    "under": "▼ under",
    "missing": "✗ missing",
    "upcoming": "· upcoming",
    "inactive": "  —",
}


def _entry_index_map(entries) -> dict[int, int]:
    return {e["_db_id"]: i for i, e in enumerate(entries)}


@click.group()
def transactions():
    """Associate transactions with budget entries and review the results."""


@transactions.command("link")
@click.argument("txn_id", type=int)
@click.argument("entry_index", type=int)
@pass_cli
def link(cli, txn_id, entry_index):
    """Link transaction TXN_ID to budget entry ENTRY_INDEX (0-based, from
    `fintrack budget`)."""
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        entries = get_budget_entries(conn, snapshot_id)
        if entry_index < 0 or entry_index >= len(entries):
            raise click.ClickException(
                f"Budget index {entry_index} out of range (0..{len(entries) - 1})"
            )
        entry = entries[entry_index]
        try:
            link_transaction(conn, snapshot_id, txn_id, entry["_db_id"])
        except SnapshotMismatch as e:
            raise click.ClickException(str(e)) from e
        except ValueError as e:
            raise click.ClickException(str(e)) from e
    click.echo(f"Linked transaction {txn_id} → {entry.get('description', '')!r}")


@transactions.command("unlink")
@click.argument("txn_id", type=int)
@pass_cli
def unlink(cli, txn_id):
    """Remove transaction TXN_ID's budget-entry link."""
    with cli.connect() as conn:
        unlink_transaction(conn, txn_id)
    click.echo(f"Unlinked transaction {txn_id}")


@transactions.command("suggest")
@click.option("--year", type=int, default=None)
@click.option("--month", type=int, default=None)
@click.option(
    "--apply",
    "apply_",
    is_flag=True,
    help="Prompt to confirm and apply each suggestion",
)
@pass_cli
def suggest(cli, year, month, apply_):
    """Propose budget-entry links for still-unlinked transactions.

    Read-only by default; --apply confirms each suggestion before linking
    (suggestions are never applied silently)."""
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        suggestions = suggest_links(conn, snapshot_id, year=year, month=month)
        entries = get_budget_entries(conn, snapshot_id)
        idx = _entry_index_map(entries)
        if not suggestions:
            click.echo("No suggestions.")
            return
        if not apply_:
            rows = [
                [
                    s.transaction["id"],
                    s.transaction["date"],
                    fmt_money(s.transaction["amount"]),
                    (s.transaction.get("merchant") or "")[:24],
                    f"[{idx.get(s.entry_ref, '?')}] {s.entry.get('description', '')}"[
                        :28
                    ],
                    f"{s.score:.2f}",
                    s.confidence,
                    ", ".join(s.reasons),
                ]
                for s in suggestions
            ]
            echo_table(
                rows,
                ["Txn", "Date", "Amount", "Merchant", "Entry", "Score", "Conf", "Why"],
                ("right", "left", "right", "left", "left", "right", "left", "left"),
            )
            click.echo(
                f"\n{len(suggestions)} suggestion(s). "
                "Apply with `transactions link <txn> <entry>` or `suggest --apply`."
            )
            return

        applied = 0
        for s in suggestions:
            merchant = s.transaction.get("merchant") or ""
            prompt = (
                f"Link txn {s.transaction['id']} "
                f"({s.transaction['date']} {fmt_money(s.transaction['amount'])} "
                f"{merchant}) → [{idx.get(s.entry_ref, '?')}] "
                f"{s.entry.get('description', '')} "
                f"[{s.confidence} {s.score:.2f}]?"
            )
            if click.confirm(prompt, default=(s.confidence == "high")):
                link_transaction(conn, snapshot_id, s.transaction["id"], s.entry_ref)
                applied += 1
        click.echo(f"\nApplied {applied} of {len(suggestions)} suggestion(s).")


@transactions.command("budget-actual")
@click.option("--year", type=int, default=None)
@click.option("--month", type=int, default=None)
@pass_cli
def budget_actual(cli, year, month):
    """Per-entry budget-vs-actual for a month (default: current): expected,
    realized, delta, and missed/upcoming recurring charges."""
    today = datetime.now().astimezone().date()
    year, month = year or today.year, month or today.month
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        actuals = budget_actuals(conn, snapshot_id, year=year, month=month, today=today)
    active = [a for a in actuals if a.status != "inactive"]
    if not active:
        click.echo(f"No active budget entries for {year}-{month:02d}.")
        return
    rows = []
    for a in active:
        drift = ""
        if a.drift_amount and abs(a.drift_amount) > 0:
            drift = fmt_money(a.drift_amount)
        rows.append(
            [
                a.entry.get("description", ""),
                a.entry.get("kind", ""),
                fmt_money(a.expected),
                fmt_money(a.actual),
                fmt_money(a.delta),
                a.count,
                _STATUS_LABEL.get(a.status, a.status),
                drift,
            ]
        )
    click.echo(f"Budget vs actual — {year}-{month:02d}")
    echo_table(
        rows,
        ["Entry", "Kind", "Expected", "Actual", "Delta", "N", "Status", "Drift"],
        ("left", "left", "right", "right", "right", "right", "left", "right"),
    )
