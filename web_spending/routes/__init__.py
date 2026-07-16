from flask import Flask


def register_blueprints(app: Flask) -> None:
    from web.routes.accounts import bp as accounts_bp
    from web.routes.imports import bp as imports_bp
    from web.routes.merchants import bp as merchants_bp
    from web.routes.transactions import bp as transactions_bp
    from web.routes.trends import bp as trends_bp

    app.register_blueprint(accounts_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(trends_bp)
    app.register_blueprint(merchants_bp)
    app.register_blueprint(imports_bp)
