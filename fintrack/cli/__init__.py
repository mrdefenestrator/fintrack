"""Unified fintrack CLI.

Currently exposes migrate-legacy; the spending and finances command trees
join this group during CLI consolidation (Phase 5).
"""

import click

from fintrack.cli.migrate_legacy import migrate_legacy


@click.group()
def cli():
    """fintrack — unified personal finance tracker."""


cli.add_command(migrate_legacy)
