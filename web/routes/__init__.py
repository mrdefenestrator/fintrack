"""Flask Blueprints for the unified fintrack web app."""

from .budget import budget_bp
from .categories import bp as categories_bp
from .edit_mode import edit_mode_bp
from .files import files_bp
from .holdings import holdings_bp
from .imports import bp as imports_bp
from .merchants import bp as merchants_bp
from .projections import projections_bp
from .status import status_bp
from .transactions import bp as transactions_bp
from .trends import bp as trends_bp


def register_blueprints(app):
    app.register_blueprint(status_bp)
    app.register_blueprint(budget_bp)
    app.register_blueprint(holdings_bp)
    app.register_blueprint(projections_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(edit_mode_bp)
    app.register_blueprint(imports_bp)
    app.register_blueprint(merchants_bp)
    app.register_blueprint(categories_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(trends_bp)
