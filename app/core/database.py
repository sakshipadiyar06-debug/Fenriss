import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    kw = {}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False}
        path = url.replace("sqlite:///", "")
        if path and ":memory:" not in path:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return create_engine(url, **kw)  # swap DATABASE_URL for PostgreSQL later


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    import app.models  # noqa: F401  (register tables)
    Base.metadata.create_all(engine)
