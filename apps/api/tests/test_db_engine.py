"""The API's engine is sized and hardened deliberately rather than left on SQLAlchemy's defaults (#524)."""
import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from src.core import db as db_module
from src.core.config import settings

URL = settings.database_url.get_secret_value()


@pytest.fixture()
def build(monkeypatch):
    engines = []

    def _build(**overrides):
        for name, value in overrides.items():
            monkeypatch.setattr(db_module, name, value)
        eng = db_module._build_engine(URL)
        engines.append(eng)
        return eng

    yield _build
    for eng in engines:
        eng.dispose()


def test_the_pool_is_sized_and_bounded_explicitly(build):
    eng = build()
    pool = eng.pool

    assert pool.size() == db_module._POOL_SIZE == 10
    assert pool._max_overflow == db_module._MAX_OVERFLOW == 10
    assert pool._timeout == db_module._POOL_TIMEOUT_SECONDS == 10
    assert pool._recycle == db_module._POOL_RECYCLE_SECONDS == 1800
    assert pool._pre_ping is True


def test_a_connect_timeout_is_set_on_new_connections(build):
    eng = build()

    with eng.connect() as conn:
        # libpq reports the options it was started with; connect_timeout is a client-side option.
        assert conn.connection.dbapi_connection.info.get_parameters()["connect_timeout"] == str(
            db_module._CONNECT_TIMEOUT_SECONDS
        )


def test_the_module_level_engine_is_built_by_the_same_helper():
    assert db_module.engine.pool._pre_ping is True
    assert db_module.engine.pool._timeout == db_module._POOL_TIMEOUT_SECONDS


def test_a_connection_killed_behind_the_pools_back_is_replaced_instead_of_failing_the_request(build):
    eng = build(_POOL_SIZE=1, _MAX_OVERFLOW=0)
    with eng.connect() as conn:
        victim_pid = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
    # The connection now sits idle in the pool; kill it the way a Postgres restart would.
    with build().connect() as killer:
        killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": victim_pid})
        killer.commit()

    with eng.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar_one() == 1
        assert conn.execute(text("SELECT pg_backend_pid()")).scalar_one() != victim_pid


def test_pool_exhaustion_fails_fast_rather_than_waiting_the_sqlalchemy_default_30s(build):
    eng = build(_POOL_SIZE=1, _MAX_OVERFLOW=0, _POOL_TIMEOUT_SECONDS=1)

    with eng.connect():
        started = time.monotonic()
        with pytest.raises(PoolTimeoutError):
            eng.connect()
        waited = time.monotonic() - started

    assert 0.9 <= waited < 5
