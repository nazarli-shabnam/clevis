"""Tests for GitHub App installation verification during sync."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.schemas.installation import SyncInstallationsInput
from src.services import installation_verify


def test_verify_sync_payload_accepts_matching_installation():
    payload = SyncInstallationsInput(
        account_login="acme",
        account_type="Organization",
        installation_id=42,
    )
    installation = {"account": {"login": "acme", "type": "Organization"}}
    with patch("src.services.installation_verify.fetch_installation", return_value=installation):
        installation_verify.verify_sync_payload(payload)


def test_verify_sync_payload_rejects_login_mismatch():
    payload = SyncInstallationsInput(
        account_login="acme",
        account_type="Organization",
        installation_id=42,
    )
    installation = {"account": {"login": "other", "type": "Organization"}}
    with patch("src.services.installation_verify.fetch_installation", return_value=installation):
        with pytest.raises(HTTPException) as exc:
            installation_verify.verify_sync_payload(payload)
    assert exc.value.status_code == 403


def test_verify_sync_payload_requires_installation_id_for_app_mode():
    payload = SyncInstallationsInput(account_login="acme", account_type="Organization")
    with pytest.raises(HTTPException) as exc:
        installation_verify.verify_sync_payload(payload)
    assert exc.value.status_code == 400
