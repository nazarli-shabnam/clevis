"""The startup notice that Row-Level Security does not apply to the API's database role."""

import logging
from unittest.mock import MagicMock, patch

from sqlalchemy import text
from fastapi.testclient import TestClient

from src.core import rls_posture
from src.core.db import engine


def _engine_returning(row):
    eng = MagicMock()
    conn = eng.connect.return_value.__enter__.return_value
    conn.execute.return_value.first.return_value = row
    return eng


def test_a_superuser_role_is_reported_as_bypassing_rls_and_warned_about(caplog):
    with caplog.at_level(logging.WARNING, logger="src.core.rls_posture"):
        assert rls_posture.warn_if_rls_bypassed(_engine_returning(("clevis", True, False))) is True
    assert "NOT enforced" in caplog.text and "'clevis'" in caplog.text
    assert "API_DB_PASSWORD" in caplog.text and "docs/self-hosting.md" in caplog.text


def test_a_bypassrls_role_is_warned_about_too(caplog):
    with caplog.at_level(logging.WARNING, logger="src.core.rls_posture"):
        assert rls_posture.warn_if_rls_bypassed(_engine_returning(("svc", False, True))) is True
    assert "'svc'" in caplog.text


def test_an_ordinary_role_gets_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="src.core.rls_posture"):
        assert rls_posture.warn_if_rls_bypassed(_engine_returning(("clevis_api", False, False))) is False
    assert caplog.text == ""


def test_an_undeterminable_posture_is_silent_and_never_raises(caplog):
    broken = MagicMock()
    broken.connect.side_effect = RuntimeError("db down")
    with caplog.at_level(logging.WARNING, logger="src.core.rls_posture"):
        assert rls_posture.warn_if_rls_bypassed(broken) is False
        assert rls_posture.warn_if_rls_bypassed(_engine_returning(None)) is False
    assert caplog.text == ""
    assert rls_posture.rls_bypassed(broken) is None


def test_the_real_connection_posture_matches_a_direct_catalog_query():
    # Role-agnostic: a local superuser and CI's constrained clevis_api role must each be reported correctly.
    with engine.connect() as conn:
        role, is_super, bypass = conn.execute(
            text("SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert rls_posture.rls_bypassed(engine) == (role, bool(is_super or bypass))


def test_startup_runs_the_check_once():
    from src.main import app

    with patch("src.main.warn_if_rls_bypassed") as warn:
        with TestClient(app):
            pass
    warn.assert_called_once_with(engine)
