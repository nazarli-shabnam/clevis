import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.config import settings
from src.models.base import Base


@pytest.fixture(scope="session")
def _engine():
    eng = create_engine(settings.database_url)
    Base.metadata.create_all(eng)  # no-op if alembic already ran
    yield eng


@pytest.fixture
def db(_engine):
    with _engine.connect() as conn:
        conn.begin()
        with Session(conn, join_transaction_mode="create_savepoint") as session:
            yield session
        conn.rollback()
