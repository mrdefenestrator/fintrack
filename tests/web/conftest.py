"""Fixtures for the generic sheet-renderer framework tests."""

import pytest
from flask import render_template

from web.app import create_app
from web import sheets


@pytest.fixture
def app(tmp_path):
    application = create_app(db_path=str(tmp_path / "test.db"), enable_sheet_demo=True)
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def render_body(app):
    """Render partials/sheet_body.html for a synthetic spec, headless.

    Returns a function(spec, groups=None, **kw) -> str. Editable specs must
    point their endpoints at real routes (the sheet_demo blueprint) so url_for
    resolves; give rows params matching those routes, e.g. {"item_id": 1}.
    """

    def _render(spec, groups=None, **kw):
        with app.test_request_context():
            ctx = sheets.render_context(spec, groups, **kw)
            return render_template("partials/sheet_body.html", **ctx)

    return _render
