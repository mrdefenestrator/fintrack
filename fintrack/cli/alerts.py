"""`fintrack alerts`: unusual spending alerts (issue #101), the CLI side of
the Overview "Needs attention" card and the Transactions "Flagged" filter."""

from datetime import date, datetime

import click

from fintrack.cli.helpers import echo_table, pass_cli
from fintrack.insights.alerts import get_alerts, is_dismissed
from fintrack.ledger.repository.alerts import (
    dismiss_category_alert,
    dismiss_transaction_alert,
    get_dismissals,
    undismiss_category_alert,
    undismiss_transaction_alert,
)

_KIND_LABELS = {
    "large_charge": "Large charge",
    "new_merchant": "New merchant",
    "running_hot": "Running hot",
}


def _today() -> date:
    return datetime.now().astimezone().date()


@click.group(invoke_without_command=True)
@click.option("--all", "show_all", is_flag=True, help="Include dismissed alerts")
@click.pass_context
def alerts(ctx, show_all):
    """Unusual spending: large charges, new merchants, categories running hot.

    Charges from the last 30 days and this month's categories, worked out
    locally from transaction history (thresholds in configs/alerts.yaml).
    With no subcommand, lists the open alerts.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(alerts_list, show_all=show_all)


@alerts.command("list")
@click.option("--all", "show_all", is_flag=True, help="Include dismissed alerts")
@pass_cli
def alerts_list(cli, show_all):
    """List open alerts (or all with --all)."""
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        found = get_alerts(conn, snapshot_id, _today(), include_dismissed=show_all)
        txn_ids, categories = get_dismissals(conn, snapshot_id)
    if not found:
        click.echo("Nothing unusual." if not show_all else "No alerts.")
        return
    rows = [
        [a["transaction_id"] or "-", _KIND_LABELS[a["kind"]], a["message"]]
        + (
            ["dismissed" if is_dismissed(a, txn_ids, categories) else "open"]
            if show_all
            else []
        )
        for a in found
    ]
    headers = ["ID", "Kind", "Alert"] + (["Status"] if show_all else [])
    echo_table(rows, headers, ("right", "left", "left") + (("left",) * show_all))
    click.echo(
        "\nDismiss with `fintrack alerts dismiss ID` "
        "or `fintrack alerts dismiss --category NAME`."
    )


def _target(transaction_id, category):
    if (transaction_id is None) == (category is None):
        raise click.UsageError("Give a transaction ID or --category, not both.")


@alerts.command("dismiss")
@click.argument("transaction_id", type=int, required=False)
@click.option("--category", help="Dismiss this category's running-hot alert")
@pass_cli
def alerts_dismiss(cli, transaction_id, category):
    """Hide an alert everywhere: a transaction's, or a category's running-hot
    alert for this month."""
    _target(transaction_id, category)
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        if category is not None:
            dismiss_category_alert(conn, snapshot_id, category, _today())
            click.echo(f"Dismissed {category} running hot this month.")
            return
        try:
            dismiss_transaction_alert(conn, snapshot_id, transaction_id)
        except LookupError as exc:
            raise click.ClickException(str(exc)) from exc
    click.echo(f"Dismissed alerts on transaction {transaction_id}.")


@alerts.command("undo")
@click.argument("transaction_id", type=int, required=False)
@click.option("--category", help="Bring back this category's running-hot alert")
@pass_cli
def alerts_undo(cli, transaction_id, category):
    """Bring back a dismissed alert."""
    _target(transaction_id, category)
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        if category is not None:
            found = undismiss_category_alert(conn, snapshot_id, category, _today())
            what = f"{category} running hot this month"
        else:
            found = undismiss_transaction_alert(conn, snapshot_id, transaction_id)
            what = f"alerts on transaction {transaction_id}"
    if not found:
        raise click.ClickException(f"Nothing dismissed for {what}.")
    click.echo(f"Brought back {what}.")
