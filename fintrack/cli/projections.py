"""`fintrack project` — multi-month balance projection."""

import click

from fintrack.cli.helpers import echo_table, pass_cli
from fintrack.core.formatting import fmt_money
from fintrack.projections import engine


@click.command()
@click.option(
    "--months",
    default=engine.DEFAULT_MONTHS,
    show_default=True,
    type=click.IntRange(engine.MIN_MONTHS, engine.MAX_MONTHS),
    help="Number of months to project (including the current month)",
)
@click.option(
    "--estimate",
    is_flag=True,
    help="Include estimated unscheduled spend (trailing 3-month category "
    "averages, minus budget-claimed categories)",
)
@pass_cli
def project(cli, months, estimate):
    """Project ending balances per account, month by month."""
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        result = engine.project(
            conn, snapshot_id, months=months, include_estimate=estimate
        )

    labels = [m["label"] for m in result["months"]]
    headers = ["Account"] + labels
    rows = []
    for row in result["rows"]:
        rows.append([row["account"]["name"]] + [fmt_money(b) for b in row["balances"]])
    if result["has_unassigned"]:
        rows.append(["(unassigned)"] + [fmt_money(v) for v in result["unassigned"]])
    rows.append(["-" for _ in headers])
    rows.append(["Liquid total"] + [fmt_money(v) for v in result["liquid"]])
    rows.append(["Net worth"] + [fmt_money(v) for v in result["net_worth"]])
    echo_table(rows, headers, ("left",) + ("right",) * len(labels))

    if result["estimate"] is not None:
        monthly = result["estimate"]["monthly"]
        click.echo(
            f"\nEstimated unscheduled spend: {fmt_money(monthly)}/month "
            f"(trailing 3-month averages)"
        )
        for category, avg in sorted(
            result["estimate"]["by_category"].items(), key=lambda kv: kv[1]
        ):
            click.echo(f"  {category}: {fmt_money(avg)}")

    for w in result["warnings"]:
        click.echo(
            f"warning: {w['account']['name']} falls to {fmt_money(w['balance'])} "
            f"in {w['month_label']} (minimum {fmt_money(w['minimum'])})",
            err=True,
        )
