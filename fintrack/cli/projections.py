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
@click.option(
    "--changes",
    is_flag=True,
    help="Show each month's change instead of month-end balances",
)
@pass_cli
def project(cli, months, estimate, changes):
    """Project ending balances per account, month by month."""
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        result = engine.project(
            conn, snapshot_id, months=months, include_estimate=estimate
        )

    summary = engine.summarize(result)
    _echo_summary(summary)

    labels = [m["label"] for m in result["months"]]
    # Each table line: (label, starting value, monthly series).
    lines = [
        (row["account"]["name"], row["start"], row["balances"])
        for row in result["rows"]
    ]
    if result["has_unassigned"]:
        lines.append(("(unassigned)", 0, result["unassigned"]))
    totals = [
        ("Liquid", result["start_liquid"], result["liquid"]),
        ("Net worth", result["start_net_worth"], result["net_worth"]),
    ]
    if changes:
        # Changes view: no "Now" column — month 0 is measured against it.
        headers = ["Account"] + labels
        rows = [
            [name] + [fmt_money(v) for v in engine.changes(start, series)]
            for name, start, series in lines
        ]
        rows.append(["-" for _ in headers])
        for name, start, series in totals:
            rows.append(
                [f"{name} change"]
                + [fmt_money(v) for v in engine.changes(start, series)]
            )
    else:
        headers = ["Account", "Now"] + labels
        rows = [
            [name, fmt_money(start)] + [fmt_money(v) for v in series]
            for name, start, series in lines
        ]
        rows.append(["-" for _ in headers])
        for name, start, series in totals:
            label = "Liquid total" if name == "Liquid" else name
            rows.append([label, fmt_money(start)] + [fmt_money(v) for v in series])
    echo_table(rows, headers, ("left",) + ("right",) * (len(headers) - 1))

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

    for w in summary["warnings"]:
        months_below = w["months_below"]
        click.echo(
            f"warning: {w['account']['name']} drops below its "
            f"{_plain(w['minimum'])} minimum in {w['first_month_label']} "
            f"(lowest {_plain(w['lowest'])}, {months_below} "
            f"month{'s' if months_below != 1 else ''} below)",
            err=True,
        )


def _plain(x) -> str:
    """Money without the table padding: $1,234.00 / ($1,234.00)."""
    return fmt_money(x).strip()


def _change(x) -> str:
    if x == 0:
        return "$0.00"
    return f"{'+' if x > 0 else '-'}${abs(x):,.2f}"


def _echo_summary(summary: dict) -> None:
    end = summary["end_label"]
    for name, key in (("Liquid", "liquid"), ("Net worth", "net_worth")):
        t = summary[key]
        click.echo(
            f"{name} by {end}: {_plain(t['end'])} "
            f"(now {_plain(t['start'])}, {_change(t['change'])}, "
            f"{_change(t['per_month'])}/mo)"
        )
    low = summary["lowest_liquid"]
    click.echo(f"Lowest liquid: {_plain(low['amount'])} in {low['month_label']}")
    if summary["payoffs"]:
        paid = ", ".join(
            f"{p['name']} ({p['month_label']})" for p in summary["payoffs"]
        )
        click.echo(f"Paid off: {paid}")
    click.echo()
