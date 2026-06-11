#!/usr/bin/env python
# -*- coding: utf-8 -*-
import base64
import hashlib
import logging
import re

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_FERNET_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+=*$")


def _derive_fernet_key(secret: str) -> bytes:
    """从配置密钥派生Fernet密钥"""
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


class ApiKeyCipherError(Exception):
    """Raised when encrypted API key data cannot be decrypted."""


class ApiKeyCipher:
    """API Key加解密工具"""

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("API_KEY_SECRET 未配置，无法初始化密钥加密器")
        self._fernet = Fernet(_derive_fernet_key(secret))

    def encrypt(self, plain: str) -> str:
        if not plain:
            return ""
        return self._fernet.encrypt(plain.encode()).decode()

    def decrypt_or_raise(self, encrypted: str) -> str:
        """Decrypt fernet_v1 ciphertext; raise on invalid token."""
        if not encrypted:
            return ""
        try:
            return self._fernet.decrypt(encrypted.encode()).decode()
        except InvalidToken as exc:
            raise ApiKeyCipherError("无法解密 LLM API Key，请检查 API_KEY_SECRET 是否正确") from exc

    @staticmethod
    def looks_like_fernet_token(value: str) -> bool:
        """Heuristic check for Fernet token shape without decrypting."""
        if not value or len(value) < 44:
            return False
        if not _FERNET_TOKEN_RE.fullmatch(value):
            return False
        try:
            padding = b"=" * ((4 - len(value) % 4) % 4)
            raw = base64.urlsafe_b64decode(value.encode() + padding)
        except Exception:
            return False
        return len(raw) >= 57 and raw[0] == 0x80

    @staticmethod
    def mask(api_key: str) -> str:
        if not api_key:
            return ""
        if len(api_key) <= 8:
            return "****"
        return api_key[:4] + "****" + api_key[-4:]
