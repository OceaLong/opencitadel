#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Hot-reloadable application configuration provider."""
import asyncio
import logging
from typing import Optional

from app.domain.models.app_config import AppConfig
from app.domain.repositories.app_config_repository import AppConfigRepository
from app.infrastructure.repositories.file_app_config_repository import FileAppConfigRepository
from core.config import get_settings

logger = logging.getLogger(__name__)

_provider: Optional["AppConfigProvider"] = None


class AppConfigProvider:
    """Load app config from repository with optional refresh."""

    def __init__(self, repository: AppConfigRepository) -> None:
        self._repository = repository
        self._cache: Optional[AppConfig] = None
        self._lock = asyncio.Lock()
        self._version = 0

    @property
    def version(self) -> int:
        return self._version

    async def get(self, *, force_reload: bool = False) -> AppConfig:
        async with self._lock:
            if self._cache is None or force_reload:
                self._cache = self._repository.load()
                self._version += 1
                logger.debug("AppConfigProvider loaded config version=%s", self._version)
            return self._cache

    async def refresh(self) -> AppConfig:
        return await self.get(force_reload=True)

    def invalidate(self) -> None:
        self._cache = None


def get_app_config_provider() -> AppConfigProvider:
    global _provider
    if _provider is None:
        settings = get_settings()
        _provider = AppConfigProvider(
            FileAppConfigRepository(settings.app_config_filepath)
        )
    return _provider
