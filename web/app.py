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

from flask import Flask, g, render_template, request

# Add project root for direct-execution mode (python web/app.py)
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fintrack.core import formatting
from fintrack.core.config import CATEGORIES_CONFIG
from fintrack.core.db import get_engine, init_db
from fintrack.core.types import ACCOUNT_TYPE_OPTIONS
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
        formatting.fmt_money(x) if x is not None else "-"
    )
    app.jinja_env.filters["fmt_money_header"] = _fmt_money_header
    app.jinja_env.filters["fmt_qty"] = formatting.fmt_qty
    app.jinja_env.filters["display_is_negative"] = lambda x: _display_is_negative(x)
    app.jinja_env.filters["format_type"] = lambda x: formatting.fmt_type_display(x)
    app.jinja_env.filters["format_recurrence"] = lambda x: (
        formatting.fmt_recurrence_display(x)
    )
    app.jinja_env.filters["format_month"] = lambda x: formatting.fmt_month_short(x)
    app.jinja_env.filters["account_label"] = formatting.fmt_account_label

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
        render_template kwargs always win over these. Quick totals (n2/n3/n6)
        are computed for full-page renders so the nav tabs show them on every
        page; HTMX partial responses never include the header, so they skip
        the extra load.
        """
        filename = getattr(g, "filename", None)
        if filename is None:
            return {}
        with engine.connect() as conn:
            available_files = list_snapshots(conn)
        defaults = {
            "filename": filename,
            "active_file": filename,
            "available_files": available_files,
            "edit_mode": False,
            "n2": None,
            "n3": None,
            "n6": None,
        }
        snapshot_id = getattr(g, "snapshot_id", None)
        if snapshot_id is not None and not request.headers.get("HX-Request"):
            from fintrack.core.loader import load_finances_from_db
            from web.routes.common import quick_totals

            with engine.connect() as conn:
                data = load_finances_from_db(conn, snapshot_id)
            defaults.update(quick_totals(data))
        return defaults

    @app.context_processor
    def _account_type_options():
        """Canonical account-type (value, label) list, available to every
        template that renders an account-type select (import quick-create,
        accounts page type editor)."""
        return {"account_type_options": ACCOUNT_TYPE_OPTIONS}

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
