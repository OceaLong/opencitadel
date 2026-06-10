#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Hot-reloadable application configuration provider."""
import asyncio
import logging
from typing import Optional

from app.application.services.app_config_repository_factory import create_app_config_repository
from app.domain.models.app_config import AppConfig
from app.domain.repositories.app_config_repository import AppConfigRepository

logger = logging.getLogger(__name__)

_provider: Optional["AppConfigProvider"] = None
_sync_cache: Optional[AppConfig] = None


def _blocking_load_config() -> AppConfig:
    repo = create_app_config_repository()
    return asyncio.run(repo.load()) or AppConfig()


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
                self._cache = await self._repository.load() or AppConfig()
                self._version += 1
                _set_sync_cache(self._cache)
                logger.debug("AppConfigProvider loaded config version=%s", self._version)
            return self._cache

    async def refresh(self) -> AppConfig:
        return await self.get(force_reload=True)

    def invalidate(self) -> None:
        self._cache = None
        invalidate_runtime_config()


def _set_sync_cache(config: AppConfig) -> None:
    global _sync_cache
    _sync_cache = config


def invalidate_runtime_config() -> None:
    """Clear synchronous and async config caches."""
    global _sync_cache
    _sync_cache = None
    if _provider is not None:
        _provider._cache = None


def get_runtime_config() -> AppConfig:
    """Synchronous accessor for runtime config (cached per process)."""
    global _sync_cache
    if _sync_cache is None:
        try:
            asyncio.get_running_loop()
            logger.warning("Runtime config cache cold inside event loop; using defaults until warmup")
            return AppConfig()
        except RuntimeError:
            _sync_cache = _blocking_load_config()
    return _sync_cache


def get_app_config_provider() -> AppConfigProvider:
    global _provider
    if _provider is None:
        _provider = AppConfigProvider(create_app_config_repository())
    return _provider
