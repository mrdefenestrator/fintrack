#!/usr/bin/env python3
"""Flask web application for finances tracker (read-only view + inline edit)."""

import os
from decimal import Decimal
from pathlib import Path

from flask import Flask, render_template

# Add project root for finances module
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import finances
from finances.db import get_engine, init_db

# Handle both direct execution (./web/app.py) and module import (from web.app import create_app)
try:
    from .routes import (
        status_bp,
        accounts_bp,
        budget_bp,
        assets_bp,
        files_bp,
        edit_mode_bp,
    )
except ImportError:
    from routes import (
        status_bp,
        accounts_bp,
        budget_bp,
        assets_bp,
        files_bp,
        edit_mode_bp,
    )

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


def create_app(db_path: str | None = None) -> Flask:
    """Application factory. Creates the engine, initialises the schema, and
    wires up filters, blueprints, and error handlers."""
    app = Flask(__name__)

    if db_path is None:
        db_path = os.environ.get("FINANCES_DB") or str(PROJECT_ROOT / "finances.db")

    engine = get_engine(db_path)
    init_db(engine)
    app.config["engine"] = engine

    _register_filters(app)

    app.register_blueprint(status_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(budget_bp)
    app.register_blueprint(assets_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(edit_mode_bp)

    @app.errorhandler(404)
    def not_found(e):
        """Render 404 with message."""
        return render_template(
            "404.html", message=e.description, n2=None, n3=None, n6=None
        ), 404

    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("FLASK_RUN_PORT", 5001))
    app.run(debug=True, host="0.0.0.0", port=port)
