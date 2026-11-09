"""`fintrack project` — the timeline: actual past month-ends and the
multi-month balance projection, in one window (same as the web Projections
page)."""

import click

from fintrack.cli.helpers import echo_table, pass_cli, plain_money, signed_money
from fintrack.core.formatting import fmt_money
from fintrack.projections import engine, timeline


@click.command()
@click.option(
    "--months",
    default=engine.DEFAULT_MONTHS,
    show_default=True,
    type=click.IntRange(engine.MIN_MONTHS, engine.MAX_MONTHS),
    help="Number of months to show (the window; by default it starts with "
    "the current month)",
)
@click.option(
    "--from",
    "start",
    metavar="YYYY-MM",
    help="First month of the window. Months before the current one show "
    "actual month-end balances; the current month on is projected",
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
@click.option(
    "--month",
    metavar="YYYY-MM",
    help="Instead of the table, list every money movement in this projected "
    "month (must be within the window, from the current month on)",
)
@pass_cli
def project(cli, months, start, estimate, changes, month):
    """Month-end balances per account: actual for past months, projected
    from the current month on."""
    start_ym = None
    if start:
        start_ym = timeline.parse_month(start)
        if start_ym is None:
            raise click.BadParameter("use YYYY-MM", param_hint="--from")
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        tl = timeline.build_timeline(
            conn,
            snapshot_id,
            start=start_ym,
            months=months,
            include_estimate=estimate,
        )
    result = tl["projection"]

    month_index = _month_index(tl, month) if month else None

    summary = tl["summary"]
    _echo_summary(summary)

    if month_index is not None:
        _echo_breakdown(engine.month_breakdown(result, month_index))
    else:
        _echo_table(tl, changes)

    if tl["has_future"]:
        _echo_estimate(result)
    if not summary["past"]:
        _echo_warnings(summary)


def _month_index(tl: dict, month: str) -> int:
    """The projection index of a YYYY-MM month in the window's projected part."""
    ym = timeline.parse_month(month)
    if ym is None:
        raise click.BadParameter("use YYYY-MM", param_hint="--month")
    projected = [m for m in tl["months"] if m["index"] is not None]
    for m in projected:
        if (m["year"], m["month"]) == ym:
            return m["index"]
    if any((m["year"], m["month"]) == ym for m in tl["months"]):
        raise click.BadParameter(
            f"{month} is in the past; the breakdown covers projected months only",
            param_hint="--month",
        )
    first, last = tl["months"][0]["label"], tl["months"][-1]["label"]
    raise click.BadParameter(
        f"{month} is outside the window ({first} to {last}); "
        "use --months or --from to move it",
        param_hint="--month",
    )


def _echo_breakdown(breakdown: dict) -> None:
    click.echo(f"What happens in {breakdown['label']}")
    totals = [
        f"Money in {signed_money(breakdown['money_in'])}",
        f"Money out {signed_money(breakdown['money_out'])}",
    ]
    if breakdown["autopay"]:
        totals.append(f"Card autopay {plain_money(breakdown['autopay'])}")
    if breakdown["holdings_change"]:
        totals.append(f"Assets & loans {signed_money(breakdown['holdings_change'])}")
    click.echo("  " + " · ".join(totals))
    if not breakdown["groups"]:
        click.echo("\nNothing changes this month.")
    for group in breakdown["groups"]:
        click.echo(f"\n{group['name']}: {signed_money(group['total'])}")
        for f in group["flows"]:
            click.echo(f"  {f['label']}: {signed_money(f['amount'])}")


def _fmt_cell(cell: dict) -> str:
    """A past or projected value: blank when nothing was recorded yet, "~"
    when carried forward from an earlier month, "*" on a partial total."""
    v = cell["value"]
    if v is None:
        return ""
    text = fmt_money(v)
    if cell.get("carried"):
        text += "~"
    if cell.get("missing"):
        text += "*"
    return text


def _echo_table(tl: dict, changes: bool) -> None:
    months = tl["months"]
    proj = tl["projection"]

    def cell(v, **extra):
        return {"value": v, **extra}

    # Each table line: (label, cells, Now value, opening value).
    lines = [
        (row["account"]["name"], row["cells"], row["start"], row["opening"])
        for row in tl["rows"]
    ]
    if tl["unassigned"] is not None:
        u = tl["unassigned"]
        lines.append(("(unassigned)", u["cells"], 0, u["opening"]))
    totals = [
        (
            name,
            [cell(t["value"], missing=t["missing"]) for t in tl[key]],
            proj[f"start_{key}"],
            change_cells,
        )
        for (name, key), change_cells in zip(
            (("Liquid", "liquid"), ("Net worth", "net_worth")),
            timeline.change_totals(tl).values(),
            strict=True,
        )
    ]

    def change_row(label, cells, now, opening):
        deltas = timeline.changes(cells, opening, months, now)
        return [label] + ["" if d is None else fmt_money(d) for d in deltas]

    def balance_row(label, cells, now):
        out = [label]
        for c, m in zip(cells, months, strict=True):
            if m["current"]:
                out.append(fmt_money(now))
            out.append(_fmt_cell(c))
        return out

    if changes:
        # Changes view: no "Now" column — the current month is measured from it.
        headers = ["Account"] + [m["label"] for m in months]
        rows = [change_row(*line) for line in lines]
        rows.append(["-" for _ in headers])
        for name, _, _, change_cells in totals:
            rows.append([f"{name} change"] + [_fmt_cell(c) for c in change_cells])
    else:
        headers = ["Account"]
        for m in months:
            if m["current"]:
                headers.append("Now")
            headers.append(m["label"])
        rows = [balance_row(name, cells, now) for name, cells, now, _ in lines]
        rows.append(["-" for _ in headers])
        for name, cells, now, _ in totals:
            label = "Liquid total" if name == "Liquid" else name
            rows.append(balance_row(label, cells, now))
    echo_table(rows, headers, ("left",) + ("right",) * (len(headers) - 1))
    if tl["has_past"]:
        first_projected = next((m for m in months if not m["past"]), None)
        note = "Past months are actual month-end balances"
        note += (
            " (* partial total)"
            if changes
            else " (~ carried forward from an earlier month, * partial total)"
        )
        if first_projected:
            note += f"; {first_projected['label']} on is projected"
        click.echo(f"\n{note}.")


def _echo_estimate(result: dict) -> None:
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


def _warning_lines(summary: dict) -> list[str]:
    """One line per account that drops below its minimum."""
    lines = []
    for w in summary["warnings"]:
        months_below = w["months_below"]
        lines.append(
            f"{w['account']['name']} drops below its "
            f"{plain_money(w['minimum'])} minimum in {w['first_month_label']} "
            f"(lowest {plain_money(w['lowest'])}, {months_below} "
            f"month{'s' if months_below != 1 else ''} below)"
        )
    return lines


def _payoff_line(summary: dict) -> str:
    paid = ", ".join(f"{p['name']} ({p['month_label']})" for p in summary["payoffs"])
    return f"Paid off: {paid}"


def _echo_warnings(summary: dict) -> None:
    for line in _warning_lines(summary):
        click.echo(f"warning: {line}", err=True)


def _echo_summary(summary: dict) -> None:
    end = summary["end_label"]
    if summary["past"]:
        # A window ending in the past: actual values then, and the change since.
        for name, key in (("Liquid", "liquid"), ("Net worth", "net_worth")):
            t = summary[key]
            partial = " (partial)" if t["partial"] else ""
            click.echo(
                f"{name} at end of {end}: {plain_money(t['end'])}{partial} "
                f"({signed_money(t['change'])} since)"
            )
        click.echo()
        return
    for name, key in (("Liquid", "liquid"), ("Net worth", "net_worth")):
        t = summary[key]
        click.echo(
            f"{name} by {end}: {plain_money(t['end'])} "
            f"(now {plain_money(t['start'])}, {signed_money(t['change'])}, "
            f"{signed_money(t['per_month'])}/mo)"
        )
    low = summary["lowest_liquid"]
    click.echo(f"Lowest liquid: {plain_money(low['amount'])} in {low['month_label']}")
    if summary["payoffs"]:
        click.echo(_payoff_line(summary))
    click.echo()
