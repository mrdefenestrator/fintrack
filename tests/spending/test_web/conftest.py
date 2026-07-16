import pytest

from web.app import create_app


@pytest.fixture
def app(tmp_path):
    db_path = tmp_path / "test.db"
    application = create_app(db_path=str(db_path))
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def conn(app):
    engine = app.config["engine"]
    with engine.connect() as connection:
        yield connection
