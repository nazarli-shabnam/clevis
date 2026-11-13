import pytest
from pydantic import ValidationError

from src.core.config import AUTH_SECRET_MIN_LENGTH, AUTH_SECRET_TOO_SHORT, Settings


def _settings(auth_secret: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@localhost/db",
        job_secret_key="k",
        auth_secret=auth_secret,
        redis_url="redis://localhost",
    )


def test_short_auth_secret_is_rejected():
    with pytest.raises(ValidationError, match=AUTH_SECRET_TOO_SHORT.replace("(", r"\(").replace(")", r"\)")):
        _settings("changeme")


def test_auth_secret_at_the_minimum_length_is_accepted():
    assert _settings("x" * AUTH_SECRET_MIN_LENGTH).auth_secret.get_secret_value() == "x" * AUTH_SECRET_MIN_LENGTH
