import os
import pathlib

os.environ["DATABASE_URL"] = "sqlite:///./data/test_fenris.db"  # must be set before the app is imported
os.environ["ENABLE_SCHEDULER"] = "false"
for _f in pathlib.Path("data").glob("test_fenris.db*"):
    _f.unlink()

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.models import User  # noqa: E402


@pytest.fixture()
def db():
    """In-memory DB with one user; the session is bound to that user like a logged-in request."""
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    u = User(email="t@example.com", password_hash="x", name="T")
    s.add(u)
    s.commit()
    s.info["uid"] = u.id
    yield s
    s.close()
