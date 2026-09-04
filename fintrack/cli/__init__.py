"""Unified fintrack CLI.

One Click tree over both domains. Global options pick the database and the
snapshot (household); snapshot-scoped commands default to the sole snapshot
when only one exists.
"""

import os

import click

from fintrack.cli.helpers import CliContext


@click.group()
@click.option(
    "--db",
    "db_path",
    default=lambda: os.environ.get("FINTRACK_DB", "fintrack.db"),
    help="SQLite database (default: $FINTRACK_DB or fintrack.db)",
)
@click.option(
    "--snapshot",
    "snapshot_name",
    default=None,
    help="Snapshot (household) to operate on; defaults to the only one",
)
@click.pass_context
def cli(ctx, db_path, snapshot_name):
    """fintrack — unified personal finance tracker."""
    ctx.obj = CliContext(db_path=db_path, snapshot_name=snapshot_name)


from fintrack.cli.ledger import (  # noqa: E402
    balance,
    categories,
    dedup,
    import_cmd,
    merchants,
    report,
    serve,
    staging,
)
from fintrack.cli.migrate_legacy import migrate_legacy  # noqa: E402
from fintrack.cli.projections import project  # noqa: E402
from fintrack.cli.networth import (  # noqa: E402
    accounts,
    assets,
    budget,
    debts,
    expenses,
    funding,
    income,
    snapshots,
    status,
)

for command in (
    status,
    snapshots,
    accounts,
    balance,
    import_cmd,
    staging,
    dedup,
    merchants,
    categories,
    report,
    budget,
    income,
    expenses,
    assets,
    debts,
    funding,
    project,
    serve,
    migrate_legacy,
):
    cli.add_command(command)
