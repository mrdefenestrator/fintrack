"""CLI entrypoint for the unified fintrack tracker.

Grows one command group per merge phase; the legacy spending.py/finances.py
entrypoints keep working until CLI consolidation (Phase 5) absorbs them.
"""

from fintrack.cli import cli

if __name__ == "__main__":
    cli()
