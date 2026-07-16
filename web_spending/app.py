import os

from flask import Flask

from spending.db import get_engine, init_db
from spending.repository.categories import seed_categories


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(__name__)

    if db_path is None:
        db_path = os.environ.get("SPENDING_DB", "spending.db")

    engine = get_engine(db_path)
    init_db(engine)
    app.config["engine"] = engine

    with engine.connect() as conn:
        seed_categories(conn, "configs/categories.yaml")

    @app.template_filter("money")
    def money_filter(value, decimals=2):
        return f"{float(value):,.{decimals}f}"

    from web.routes import register_blueprints

    register_blueprints(app)

    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("SPENDING_PORT", 5002))
    debug = os.environ.get("FLASK_DEBUG", "1") != "0"
    app.run(debug=debug, port=port)
