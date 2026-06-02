#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Secret manager abstraction for API keys and credentials."""
import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class SecretManager(ABC):
    @abstractmethod
    async def get_secret(self, key: str) -> Optional[str]:
        raise NotImplementedError


class EnvSecretManager(SecretManager):
    """Read secrets from environment variables (default)."""

    async def get_secret(self, key: str) -> Optional[str]:
        return os.environ.get(key)


class VaultSecretManager(SecretManager):
    """HashiCorp Vault stub — falls back to env until Vault is configured."""

    def __init__(self, vault_addr: str = "", vault_token: str = "") -> None:
        self._vault_addr = vault_addr
        self._vault_token = vault_token
        self._fallback = EnvSecretManager()

    async def get_secret(self, key: str) -> Optional[str]:
        if not self._vault_addr:
            return await self._fallback.get_secret(key)
        logger.warning("Vault integration stub: falling back to env for key=%s", key)
        return await self._fallback.get_secret(key)


def get_secret_manager() -> SecretManager:
    from core.config import get_settings
    settings = get_settings()
    if settings.vault_addr:
        return VaultSecretManager(settings.vault_addr, settings.vault_token)
    return EnvSecretManager()
