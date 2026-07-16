#!/usr/bin/env python3
"""
CLI entrypoint for finances tracker.

Usage:
  python finances.py <snapshot> status
  python finances.py <snapshot> accounts [ -i TYPE ] [ -x TYPE ]
  python finances.py <snapshot> budget [ --kind income|expense ] [ --annual ]
  python finances.py <snapshot> assets [ --kind asset|debt ]
  python finances.py <snapshot> income add --description Salary --amount 5000 --recurrence monthly

The snapshot name corresponds to a row in the SQLite database (default: finances.db,
or set $FINANCES_DB). Import YAML data first with:
  uv run python -m finances.yaml_import data/finances.yaml

Shared logic (loader, calculations, filters, formatting, tables) lives in the
finances package (finances/). This script delegates to finances.cli.main().
"""

import sys

if __name__ == "__main__":
    from finances.cli import main

    sys.exit(main())
