"""Round-trip and backward-compatibility tests for token encryption (checks.crypto)."""

import base64
import hashlib

from cryptography.fernet import Fernet

from checks.crypto import decrypt_job_token, encrypt_job_token

_KEY = "test-job-secret-key"


def _legacy_encrypt(token: str, key: str) -> str:
    """Mirrors the pre-fix unsalted single-round SHA-256 derivation, to prove
    already-encrypted (pre-migration) ciphertext still decrypts correctly."""
    derived = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
    return Fernet(derived).encrypt(token.encode()).decode()


def test_round_trip_new_format():
    encrypted = encrypt_job_token("ghp_supersecret", _KEY)
    assert encrypted.startswith("v2:")
    assert decrypt_job_token(encrypted, _KEY) == "ghp_supersecret"


def test_decrypts_legacy_unprefixed_ciphertext():
    legacy_ciphertext = _legacy_encrypt("ghp_oldtoken", _KEY)
    assert decrypt_job_token(legacy_ciphertext, _KEY) == "ghp_oldtoken"


def test_wrong_key_fails_new_format():
    encrypted = encrypt_job_token("ghp_supersecret", _KEY)
    try:
        decrypt_job_token(encrypted, "wrong-key")
        assert False, "expected decryption to fail with the wrong key"
    except Exception:
        pass
