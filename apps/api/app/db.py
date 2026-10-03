from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

settings.storage_dir.mkdir(parents=True, exist_ok=True)
engine = create_engine(settings.vto_database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as db:
        yield db


def init_db():
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns():
    """Tiny in-place migration for the dev SQLite DB, so existing rows (e.g. the credit ledger) are kept."""
    from sqlalchemy import inspect, text

    wanted = {"products": {"category": "VARCHAR(32) DEFAULT 'other'"}, "tryon_jobs": {"run_id": "INTEGER"}}
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in wanted.items():
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
