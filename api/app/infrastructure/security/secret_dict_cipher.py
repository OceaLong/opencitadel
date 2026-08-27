"""Encrypt/decrypt secret values in MCP headers/env dicts and URLs."""

from typing import Any
from urllib.parse import urlparse

from app.application.ports.crypto import VersionedSecretCipher
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption

_SECRET_KEY_HINTS = (
    "authorization",
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "bearer",
    "access",
    "credential",
    "cred",
    "sign",
    "cookie",
    "session",
    "passwd",
    "pwd",
    "client_secret",
    "auth",
)


def _looks_secret(key: str, value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    lowered = key.lower()
    if any(hint in lowered for hint in _SECRET_KEY_HINTS):
        return True
    return bool(lowered == "key" or lowered.endswith(("_key", "_secret")))


def _url_has_sensitive_parts(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except (OSError, RuntimeError, ValueError):
        return False
    if parsed.query:
        return True
    return bool(parsed.password)


def encrypt_secret_dict(
    data: dict[str, Any] | None, cipher: VersionedSecretCipher
) -> tuple[dict[str, Any] | None, str]:
    if not data:
        return data, ApiKeyEncryption.PLAINTEXT
    encrypted: dict[str, Any] = {}
    used_encryption = False
    for key, value in data.items():
        if _looks_secret(key, value):
            encrypted[key] = cipher.encrypt_versioned(str(value))
            used_encryption = True
        else:
            encrypted[key] = value
    return encrypted, (
        ApiKeyEncryption.FERNET_V2 if used_encryption else ApiKeyEncryption.PLAINTEXT
    )


def decrypt_secret_dict(
    data: dict[str, Any] | None,
    encryption: str,
    cipher: VersionedSecretCipher,
) -> dict[str, Any] | None:
    if not data:
        return data
    if encryption == ApiKeyEncryption.PLAINTEXT:
        return data
    if encryption != ApiKeyEncryption.FERNET_V2:
        raise ValueError(f"unsupported secret dictionary encryption: {encryption}")
    decrypted: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str) and _looks_secret(key, value):
            decrypted[key] = cipher.decrypt_versioned(value)
        else:
            decrypted[key] = value
    return decrypted


def encrypt_url(url: str | None, cipher: VersionedSecretCipher) -> tuple[str | None, str]:
    if not url:
        return url, ApiKeyEncryption.PLAINTEXT
    if _url_has_sensitive_parts(url):
        return cipher.encrypt_versioned(url), ApiKeyEncryption.FERNET_V2
    return url, ApiKeyEncryption.PLAINTEXT


def decrypt_url(
    stored: str | None,
    encryption: str,
    cipher: VersionedSecretCipher,
) -> str | None:
    if not stored:
        return stored
    if encryption == ApiKeyEncryption.PLAINTEXT:
        return stored
    if encryption != ApiKeyEncryption.FERNET_V2:
        raise ValueError(f"unsupported URL encryption: {encryption}")
    return cipher.decrypt_versioned(stored)
