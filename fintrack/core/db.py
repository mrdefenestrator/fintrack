"""Database engine factory for fintrack SQLite storage."""

from pathlib import Path

from sqlalchemy import Engine, create_engine, event

from fintrack.core.models import metadata


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """SQLite ignores foreign-key constraints unless enabled per connection.

    Registered on the base Engine so every engine (app, CLI, and the engines
    tests build directly with create_engine) enforces FKs and ON DELETE CASCADE.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_engine(db_path: str | Path = "fintrack.db") -> Engine:
    return create_engine(f"sqlite:///{db_path}", future=True)


def init_db(engine: Engine) -> None:
    metadata.create_all(engine)
