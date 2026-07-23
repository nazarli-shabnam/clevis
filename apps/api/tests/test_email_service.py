"""Tests for src.services.email (issue #217)."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from src.core.config import settings
from src.services.email import EmailNotConfigured, send_verification_email


def test_raises_not_configured_when_smtp_host_unset(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", None)
    monkeypatch.setattr(settings, "smtp_from", "clevis@example.com")
    with pytest.raises(EmailNotConfigured):
        send_verification_email("user@example.com", "https://app.example.com/verify-email?token=x")


def test_raises_not_configured_when_smtp_from_unset(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", None)
    with pytest.raises(EmailNotConfigured):
        send_verification_email("user@example.com", "https://app.example.com/verify-email?token=x")


def test_sends_via_smtp_with_starttls_and_login(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_from", "clevis@example.com")
    monkeypatch.setattr(settings, "smtp_user", "smtp-user")
    monkeypatch.setattr(settings, "smtp_password", SecretStr("smtp-pass"))

    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)

    with patch("src.services.email.smtplib.SMTP", return_value=mock_smtp) as mock_cls:
        send_verification_email("user@example.com", "https://app.example.com/verify-email?token=abc")

    mock_cls.assert_called_once_with("smtp.example.com", 587, timeout=10)
    mock_smtp.starttls.assert_called_once()
    mock_smtp.login.assert_called_once_with("smtp-user", "smtp-pass")
    mock_smtp.send_message.assert_called_once()
    sent_message = mock_smtp.send_message.call_args[0][0]
    assert sent_message["To"] == "user@example.com"
    assert sent_message["From"] == "clevis@example.com"
    assert "verify-email?token=abc" in sent_message.get_content()


def test_sends_without_login_when_no_smtp_credentials(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "clevis@example.com")
    monkeypatch.setattr(settings, "smtp_user", None)
    monkeypatch.setattr(settings, "smtp_password", None)

    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)

    with patch("src.services.email.smtplib.SMTP", return_value=mock_smtp):
        send_verification_email("user@example.com", "https://app.example.com/verify-email?token=abc")

    mock_smtp.login.assert_not_called()
    mock_smtp.send_message.assert_called_once()
