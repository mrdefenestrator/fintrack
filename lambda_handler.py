"""AWS Lambda handler for PR preview environments.

Wraps the Flask app with Mangum so it can serve behind a Lambda Function URL.
On cold start, runs migrations and seeds the example household into an
ephemeral SQLite database in /tmp.
"""

import os
import subprocess
import sys

# Point the DB at Lambda's writable /tmp
os.environ.setdefault("FINTRACK_DB", "/tmp/fintrack.db")

from mangum import Mangum

from web.app import create_app

_app = None
_initialized = False


def _cold_start():
    """Run migrations and seed on first invocation."""
    global _initialized
    if _initialized:
        return
    db_path = os.environ["FINTRACK_DB"]
    if not os.path.exists(db_path):
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
        )
        subprocess.run(
            [sys.executable, "scripts/seed_example.py"],
            check=True,
        )
    _initialized = True


def handler(event, context):
    """Lambda entry point."""
    global _app
    _cold_start()
    if _app is None:
        _app = create_app()
    mangum_handler = Mangum(_app, lifespan="off")
    return mangum_handler(event, context)
