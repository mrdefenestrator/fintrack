"""Flask Blueprints for the unified fintrack web app."""

from .budget import ACTUAL_SHEET, budget_bp
from .categories import CATEGORIES_SHEET
from .categories import bp as categories_bp
from .edit_mode import edit_mode_bp
from .files import files_bp
from .holdings import holdings_bp
from .imports import bp as imports_bp
from .merchants import MERCHANTS_SHEET
from .merchants import bp as merchants_bp
from .overview import overview_bp
from .projections import projections_bp
from .status import status_bp
from .transactions import TRANSACTIONS_SHEET
from .transactions import bp as transactions_bp
from .trends import bp as trends_bp

# Column metadata of the unbanded grouped sheets, as a Jinja global: their row
# partials are rendered by many routes (row/cell swaps), so every render sees
# the same column keys without each route threading them through.
FLAT_SHEETS = {
    "transactions": TRANSACTIONS_SHEET,
    "merchants": MERCHANTS_SHEET,
    "categories": CATEGORIES_SHEET,
    "actual": ACTUAL_SHEET,
}


def register_blueprints(app):
    app.jinja_env.globals["flat_sheets"] = FLAT_SHEETS
    app.register_blueprint(status_bp)
    app.register_blueprint(overview_bp)
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
