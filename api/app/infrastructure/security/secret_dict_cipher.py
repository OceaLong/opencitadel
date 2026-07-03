#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Encrypt/decrypt secret values in MCP headers/env dicts and URLs."""
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from app.infrastructure.security.api_key_cipher import ApiKeyCipher
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
    if lowered == "key" or lowered.endswith("_key") or lowered.endswith("_secret"):
        return True
    return False


def _url_has_sensitive_parts(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.query:
        return True
    if parsed.password:
        return True
    return False


def encrypt_secret_dict(data: Optional[Dict[str, Any]], cipher: ApiKeyCipher) -> tuple[Optional[Dict[str, Any]], str]:
    if not data:
        return data, ApiKeyEncryption.LEGACY_PLAINTEXT
    encrypted: Dict[str, Any] = {}
    used_encryption = False
    for key, value in data.items():
        if _looks_secret(key, value):
            encrypted[key] = cipher.encrypt(str(value))
            used_encryption = True
        else:
            encrypted[key] = value
    return encrypted, ApiKeyEncryption.FERNET_V1 if used_encryption else ApiKeyEncryption.LEGACY_PLAINTEXT


def decrypt_secret_dict(
    data: Optional[Dict[str, Any]],
    encryption: str,
    cipher: ApiKeyCipher,
) -> Optional[Dict[str, Any]]:
    if not data:
        return data
    if encryption == ApiKeyEncryption.LEGACY_PLAINTEXT:
        return data
    decrypted: Dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str) and _looks_secret(key, value):
            try:
                decrypted[key] = cipher.decrypt_or_raise(value)
            except Exception:
                decrypted[key] = value
        else:
            decrypted[key] = value
    return decrypted


def encrypt_url(url: Optional[str], cipher: ApiKeyCipher) -> tuple[Optional[str], str]:
    if not url:
        return url, ApiKeyEncryption.LEGACY_PLAINTEXT
    if _url_has_sensitive_parts(url):
        return cipher.encrypt(url), ApiKeyEncryption.FERNET_V1
    return url, ApiKeyEncryption.LEGACY_PLAINTEXT


def decrypt_url(stored: Optional[str], encryption: str, cipher: ApiKeyCipher) -> Optional[str]:
    if not stored:
        return stored
    if encryption == ApiKeyEncryption.LEGACY_PLAINTEXT:
        return stored
    try:
        return cipher.decrypt_or_raise(stored)
    except Exception:
        return stored
