"""`fintrack recurring` (issue #100): recurring charges not in the budget and
recurring price changes — the CLI side of the Budget sheet's Recurring view.
"""

import click

from fintrack.budget import recurring as rec
from fintrack.budget.repository import get_budget_entries
from fintrack.cli.helpers import echo_table, pass_cli, plain_money

_STATUS_LABEL = {
    "not_budgeted": "✗ not in budget",
    "unlinked": "· not linked",
    "covered": "· by category",
    "linked": "✓ in budget",
}

_KINDS = click.Choice(["expense", "income"])


def _change(c: rec.RecurringCharge) -> str:
    p = c.pattern
    if p.price_change_since is None or p.previous_amount is None:
        return ""
    return f"{plain_money(p.previous_amount)} → {plain_money(p.amount)} since {p.price_change_since:%b %d}"


@click.group(invoke_without_command=True)
@click.option("--all", "show_all", is_flag=True, help="Include budgeted series")
@click.option(
    "--dismissed", is_flag=True, help="Include merchants marked not recurring"
)
@click.pass_context
def recurring(ctx, show_all, dismissed):
    """Recurring charges: those not in the budget, and price changes.

    With no subcommand, lists what needs attention (--all for every series).
    Add one with `recurring add MERCHANT`, link it to an existing entry with
    `recurring link MERCHANT ENTRY_INDEX`, or hide it with `recurring dismiss`.
    Detection is local; no transaction data leaves the machine."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(list_cmd, show_all=show_all, dismissed=dismissed)


@recurring.command("list")
@click.option("--all", "show_all", is_flag=True, help="Include budgeted series")
@click.option(
    "--dismissed", is_flag=True, help="Include merchants marked not recurring"
)
@pass_cli
def list_cmd(cli, show_all, dismissed):
    """List recurring series (default: only those needing attention)."""
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        charges = rec.find_recurring(conn, snapshot_id, include_dismissed=dismissed)
        entries = get_budget_entries(conn, snapshot_id)
    idx = {e["_db_id"]: i for i, e in enumerate(entries)}
    shown = charges if show_all else rec.attention_items(charges)
    if not shown:
        click.echo(
            "No recurring charges need attention."
            + ("" if show_all else " (--all lists every series)")
        )
        return
    rows = []
    for c in shown:
        budget = _STATUS_LABEL[c.status]
        if c.entry_ref is not None:
            budget += f" [{idx.get(c.entry_ref, '?')}] {c.entry_description or ''}"
        rows.append(
            [
                c.merchant[:28],
                c.kind,
                c.category or "",
                plain_money(c.amount),
                c.cadence.key,
                c.pattern.count,
                c.pattern.last_date,
                c.pattern.next_date,
                budget[:36],
                _change(c),
            ]
        )
    echo_table(
        rows,
        [
            "Merchant",
            "Kind",
            "Category",
            "Typical",
            "Every",
            "N",
            "Last",
            "Next",
            "Budget",
            "Price change",
        ],
        (
            "left",
            "left",
            "left",
            "right",
            "left",
            "right",
            "left",
            "left",
            "left",
            "left",
        ),
    )


@recurring.command("add")
@click.argument("merchant")
@click.option("--kind", type=_KINDS, default="expense", show_default=True)
@pass_cli
def add_cmd(cli, merchant, kind):
    """Add MERCHANT's recurring series to the budget and link its
    transactions (amount, recurrence, day, category and account pre-filled)."""
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        try:
            charge = rec.get_recurring(conn, snapshot_id, merchant, kind)
            rec.add_to_budget(conn, snapshot_id, merchant, kind)
        except rec.NotRecurring as e:
            raise click.ClickException(str(e)) from e
    click.echo(
        f"Added {merchant!r} to the budget: {plain_money(charge.pattern.amount)} "
        f"{charge.cadence.recurrence}; linked {len(charge.transaction_ids)} "
        "transaction(s)."
    )


@recurring.command("link")
@click.argument("merchant")
@click.argument("entry_index", type=int)
@click.option("--kind", type=_KINDS, default="expense", show_default=True)
@pass_cli
def link_cmd(cli, merchant, entry_index, kind):
    """Link MERCHANT's recurring transactions to budget entry ENTRY_INDEX
    (0-based, from `fintrack budget`)."""
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        entries = get_budget_entries(conn, snapshot_id)
        if not 0 <= entry_index < len(entries):
            raise click.ClickException(
                f"Budget index {entry_index} out of range (0..{len(entries) - 1})"
            )
        entry = entries[entry_index]
        try:
            n = rec.link_to_entry(conn, snapshot_id, merchant, entry["_db_id"], kind)
        except ValueError as e:  # NotRecurring, SnapshotMismatch
            raise click.ClickException(str(e)) from e
    click.echo(f"Linked {n} transaction(s) of {merchant!r} → {entry['description']!r}")


@recurring.command("dismiss")
@click.argument("merchant")
@pass_cli
def dismiss_cmd(cli, merchant):
    """Mark MERCHANT "not recurring" so it stops showing."""
    with cli.connect() as conn:
        rec.dismiss(conn, cli.snapshot_id(conn), merchant)
    click.echo(f"Marked {merchant!r} not recurring.")


@recurring.command("undismiss")
@click.argument("merchant")
@pass_cli
def undismiss_cmd(cli, merchant):
    """Show MERCHANT again (forget a "not recurring" mark)."""
    with cli.connect() as conn:
        removed = rec.undismiss(conn, cli.snapshot_id(conn), merchant)
    click.echo(
        f"{merchant!r} will show again."
        if removed
        else f"{merchant!r} wasn't dismissed."
    )
