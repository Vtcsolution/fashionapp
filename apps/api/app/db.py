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
