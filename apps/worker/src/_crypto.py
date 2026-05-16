import base64
import hashlib

from cryptography.fernet import Fernet


def _fernet(raw_key: str) -> Fernet:
    derived = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode()).digest())
    return Fernet(derived)


def encrypt_job_token(token: str, key: str) -> str:
    return _fernet(key).encrypt(token.encode()).decode()


def decrypt_job_token(encrypted: str, key: str) -> str:
    return _fernet(key).decrypt(encrypted.encode()).decode()
