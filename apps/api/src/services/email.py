"""Outbound email — currently just the /auth/register verification link (issue #217).

SMTP is optional (see src.core.config.Settings). If unconfigured, send_verification_email
raises EmailNotConfigured and callers are expected to degrade gracefully -- account creation
must never fail because email sending isn't set up.
"""

import smtplib
from email.message import EmailMessage

from src.core.config import settings


class EmailNotConfigured(RuntimeError):
    """Raised when SMTP isn't configured. Mirrors GitHubOAuthNotConfigured
    (src.services.github_oauth) -- callers decide how to degrade."""


def send_verification_email(to_email: str, verify_url: str) -> None:
    if not settings.smtp_host or not settings.smtp_from:
        raise EmailNotConfigured("SMTP_HOST and SMTP_FROM must be set to send verification emails")

    message = EmailMessage()
    message["Subject"] = "Verify your Clevis email address"
    message["From"] = settings.smtp_from
    message["To"] = to_email
    message.set_content(
        "Welcome to Clevis!\n\n"
        "Verify your email address to accept organization invitations:\n"
        f"{verify_url}\n\n"
        "If you didn't create this account, you can ignore this email."
    )

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.starttls()
        if settings.smtp_user and settings.smtp_password:
            smtp.login(settings.smtp_user, settings.smtp_password.get_secret_value())
        smtp.send_message(message)
