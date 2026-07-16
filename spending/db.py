from pathlib import Path

from sqlalchemy import Engine, create_engine

from spending.models import metadata


def get_engine(db_path: str | Path = "spending.db") -> Engine:
    return create_engine(f"sqlite:///{db_path}")


def init_db(engine: Engine) -> None:
    metadata.create_all(engine)
