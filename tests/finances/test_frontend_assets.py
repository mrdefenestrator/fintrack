"""The app warns when the front-end build (web/static/dist/) hasn't run."""

import logging

import web.app as web_app


def test_warns_when_compiled_css_missing(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(web_app, "COMPILED_CSS", tmp_path / "missing.css")
    monkeypatch.setattr(web_app, "PROJECT_ROOT", tmp_path)
    with caplog.at_level(logging.WARNING):
        web_app.create_app(db_path=":memory:")
    assert "Front-end assets not built" in caplog.text
    assert "mise run assets" in caplog.text


def test_silent_when_compiled_css_present(tmp_path, monkeypatch, caplog):
    css = tmp_path / "tailwind.css"
    css.write_text("")
    monkeypatch.setattr(web_app, "COMPILED_CSS", css)
    with caplog.at_level(logging.WARNING):
        web_app.create_app(db_path=":memory:")
    assert "Front-end assets not built" not in caplog.text
