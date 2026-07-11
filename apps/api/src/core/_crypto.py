import base64
import hashlib

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Fixed application-level salt for the PBKDF2 key derivation below. It isn't secret — its
# job is to make the derived key resistant to precomputed-hash attacks, not to add
# per-ciphertext randomness (JOB_SECRET_KEY itself is what must stay secret).
_PBKDF2_SALT = b"clevis-job-token-fernet-v2"
_PBKDF2_ITERATIONS = 480_000
_V2_PREFIX = "v2:"


def _legacy_fernet(raw_key: str) -> Fernet:
    """Unsalted single-round SHA-256 derivation used before the v2 scheme below. Kept only
    to decrypt ciphertext persisted before this change; never used for new encryption."""
    derived = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode()).digest())
    return Fernet(derived)


def _fernet_v2(raw_key: str) -> Fernet:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=_PBKDF2_SALT, iterations=_PBKDF2_ITERATIONS)
    derived = base64.urlsafe_b64encode(kdf.derive(raw_key.encode()))
    return Fernet(derived)


def encrypt_job_token(token: str, key: str) -> str:
    return _V2_PREFIX + _fernet_v2(key).encrypt(token.encode()).decode()


def decrypt_job_token(encrypted: str, key: str) -> str:
    if encrypted.startswith(_V2_PREFIX):
        return _fernet_v2(key).decrypt(encrypted[len(_V2_PREFIX):].encode()).decode()
    # Un-prefixed ciphertext predates the v2 scheme — fall back to the legacy derivation.
    return _legacy_fernet(key).decrypt(encrypted.encode()).decode()
