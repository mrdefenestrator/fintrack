#!/bin/sh
set -e

mkdir -p /app/data

# Adopt pre-Alembic databases before migrating. A DB created by the dev
# server (init_db/create_all) has the baseline schema but no alembic_version
# table; `alembic upgrade head` on it would try to re-create existing tables
# and crash the container. Detect that state and stamp the baseline revision
# (6a88702b7507 == the exact schema create_all used to produce) so upgrades
# apply only the migrations that came after it.
.venv/bin/python - <<'PY'
import os
import sqlite3
import subprocess

db = os.environ.get("FINTRACK_DB", "fintrack.db")
if os.path.exists(db):
    con = sqlite3.connect(db)
    tables = {
        r[0]
        for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    con.close()
    if "snapshots" in tables and "alembic_version" not in tables:
        print("Pre-Alembic database detected; stamping baseline revision")
        subprocess.run(
            [".venv/bin/alembic", "stamp", "6a88702b7507"], check=True
        )
PY

.venv/bin/alembic upgrade head

exec .venv/bin/flask --app web/app.py run --host 0.0.0.0 --port 5003
