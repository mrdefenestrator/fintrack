#!/usr/bin/env python3
"""Unified fintrack web application.

One Flask app serving both domains: the net-worth pages (status, accounts,
budget, assets — finances-style explicit filename views with spreadsheet
editing) and the ledger pages (transactions, trends, merchants, import —
snapshot-scoped HTMX partial swaps). URL scheme: / is the snapshot picker;
everything else lives under /s/<snapshot>/<section>.
"""

import os
from decimal import Decimal
from pathlib import Path

from flask import Flask, g, render_template

# Add project root for direct-execution mode (python web/app.py)
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fintrack import finances_compat as finances
from fintrack.core.config import CATEGORIES_CONFIG
from fintrack.core.db import get_engine, init_db
from fintrack.ledger.repository.categories import seed_categories
from fintrack.snapshots.repository import list_snapshots

try:
    from .routes import register_blueprints
except ImportError:
    from routes import register_blueprints

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _display_is_negative(value):
    """True if value is a number < 0 or a string that looks like a negative (e.g. ($1.00) or -1)."""
    if isinstance(value, (int, float, Decimal)):
        return value < 0
    if isinstance(value, str):
        s = value.strip()
        return s.startswith("(") or (s.startswith("-") and len(s) > 1)
    return False


def _fmt_money_header(x):
    """Format money for the page header: accounting parens for negatives, no trailing spaces."""
    if x is None:
        return ""
    if x == 0:
        return "$0.00"
    if x < 0:
        return f"(${abs(x):,.2f})"
    return f"${x:,.2f}"


def _register_filters(app: Flask) -> None:
    app.jinja_env.filters["fmt_money"] = lambda x: (
        finances.fmt_money(x) if x is not None else "-"
    )
    app.jinja_env.filters["fmt_money_header"] = _fmt_money_header
    app.jinja_env.filters["fmt_qty"] = finances.fmt_qty
    app.jinja_env.filters["display_is_negative"] = lambda x: _display_is_negative(x)
    app.jinja_env.filters["format_type"] = lambda x: finances.fmt_type_display(x)
    app.jinja_env.filters["format_recurrence"] = lambda x: (
        finances.fmt_recurrence_display(x)
    )
    app.jinja_env.filters["format_month"] = lambda x: finances.fmt_month_short(x)

    @app.template_filter("money")
    def money_filter(value, decimals=2):
        return f"{float(value):,.{decimals}f}"


def create_app(db_path: str | None = None) -> Flask:
    """Application factory. Creates the engine, initialises the schema, and
    wires up filters, blueprints, and error handlers."""
    app = Flask(__name__)

    if db_path is None:
        db_path = os.environ.get("FINTRACK_DB") or str(PROJECT_ROOT / "fintrack.db")

    engine = get_engine(db_path)
    init_db(engine)
    app.config["engine"] = engine

    with engine.connect() as conn:
        seed_categories(conn, CATEGORIES_CONFIG)

    _register_filters(app)
    register_blueprints(app)

    @app.context_processor
    def _base_defaults():
        """Chrome defaults for pages that don't build the finances context.

        The snapshot-scoped ledger blueprints populate g; explicit
        render_template kwargs always win over these.
        """
        filename = getattr(g, "filename", None)
        if filename is None:
            return {}
        with engine.connect() as conn:
            available_files = list_snapshots(conn)
        return {
            "filename": filename,
            "active_file": filename,
            "available_files": available_files,
            "edit_mode": False,
            "n2": None,
            "n3": None,
            "n6": None,
        }

    @app.errorhandler(404)
    def not_found(e):
        """Render 404 with message."""
        return render_template(
            "404.html", message=e.description, n2=None, n3=None, n6=None
        ), 404

    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("FINTRACK_PORT", 5003))
    debug = os.environ.get("FLASK_DEBUG", "1") != "0"
    app.run(debug=debug, host="0.0.0.0", port=port)
