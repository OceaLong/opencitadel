#!/usr/bin/env python
# -*- coding: utf-8 -*-
import base64
import hashlib
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


def _derive_fernet_key(secret: str) -> bytes:
    """从配置密钥派生Fernet密钥"""
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


_DEFAULT_DEV_SECRET = "my-manus-api-key-secret-change-in-production"


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

    def decrypt(self, encrypted: str) -> str:
        if not encrypted:
            return ""
        try:
            return self._fernet.decrypt(encrypted.encode()).decode()
        except InvalidToken:
            # 兼容未加密的旧数据
            return encrypted

    @staticmethod
    def mask(api_key: str) -> str:
        if not api_key:
            return ""
        if len(api_key) <= 8:
            return "****"
        return api_key[:4] + "****" + api_key[-4:]
