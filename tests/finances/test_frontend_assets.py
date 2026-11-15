"""Front-end asset serving: the build-missing warning, the separate static
host (FINTRACK_STATIC_URL), and the Lambda handler's static-only mode."""

import logging
import re

import pytest

import web.app as web_app

STATIC_HOST = "https://static.example.test"


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


def _static_refs(html: str) -> list[str]:
    return re.findall(r'(?:src|href)="([^"]*/static/[^"]*)"', html)


def test_base_page_references_no_third_party_hosts(monkeypatch):
    monkeypatch.delenv("FINTRACK_STATIC_URL", raising=False)
    html = web_app.create_app(db_path=":memory:").test_client().get("/").text
    assert not re.search(r'(?:src|href)="(?:https?:)?//', html)
    refs = _static_refs(html)
    assert any(r.endswith("dist/tailwind.css") for r in refs)
    assert any(r.endswith("dist/alpine.min.js") for r in refs)
    assert all(r.startswith("/static/") for r in refs)


def test_static_url_prefix_moves_static_files_to_that_host(monkeypatch):
    monkeypatch.setenv("FINTRACK_STATIC_URL", STATIC_HOST + "/")
    app = web_app.create_app(db_path=":memory:")
    html = app.test_client().get("/").text
    refs = _static_refs(html)
    assert refs
    for ref in refs:
        if ref.endswith("manifest.webmanifest"):
            # start_url/scope must resolve on the app's own origin.
            assert ref == "/static/manifest.webmanifest"
        else:
            assert ref.startswith(STATIC_HOST + "/static/"), ref
    with app.test_request_context():
        # Non-static endpoints and _external URLs are untouched.
        assert app.url_for("files.new") == "/files/new"
        assert app.url_for("static", filename="favicon.svg", _external=True).startswith(
            "http://localhost/"
        )


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setenv("FINTRACK_DB", ":memory:")
    import lambda_handler

    monkeypatch.setattr(lambda_handler, "_STATIC_ONLY", True)
    monkeypatch.setattr(lambda_handler, "_app", None)
    monkeypatch.setattr(
        lambda_handler,
        "_cold_start",
        lambda: pytest.fail("static-only mode must not migrate/seed"),
    )
    return lambda_handler.handler


def _event(path: str) -> dict:
    return {
        "requestContext": {"http": {"method": "GET", "path": path}},
        "headers": {"host": "static.example.test"},
        "rawQueryString": "",
    }


def test_static_only_handler_serves_static_files(handler):
    resp = handler(_event("/static/js/sheet-scroll.js"), None)
    assert resp["statusCode"] == 200
    assert "javascript" in resp["headers"]["Content-Type"]
    assert resp["isBase64Encoded"] is False


def test_static_only_handler_refuses_app_pages(handler):
    assert handler(_event("/"), None)["statusCode"] == 404
    assert handler(_event("/s/Dense/holdings"), None)["statusCode"] == 404
