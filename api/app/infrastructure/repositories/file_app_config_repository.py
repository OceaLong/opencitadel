#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
from pathlib import Path
from typing import List, Optional

import yaml
from filelock import FileLock

from app.application.errors.exceptions import NotFoundError, ServerRequestsError
from app.domain.models.app_config import AppConfig
from app.domain.models.app_config_revision import AppConfigRevision
from app.domain.repositories.app_config_repository import AppConfigRepository

logger = logging.getLogger(__name__)


class FileAppConfigRepository(AppConfigRepository):
    """基于本地文件的App配置数据仓库（仅 global，不支持 user 覆盖与版本历史）"""

    def __init__(self, config_path: str) -> None:
        root_dir = Path.cwd()
        self._config_path = root_dir.joinpath(root_dir, config_path)
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = self._config_path.with_suffix(".lock")

    def _create_default_app_config_if_not_exists(self) -> None:
        if not self._config_path.exists():
            default_app_config = AppConfig()
            self._save_sync(default_app_config)

    def _load_sync(self) -> Optional[AppConfig]:
        self._create_default_app_config_if_not_exists()
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return AppConfig.model_validate(data) if data else None
        except Exception as e:
            logger.error(f"读取应用配置失败: {str(e)}")
            raise ServerRequestsError("读取应用配置失败，请稍后尝试")

    def _save_sync(self, app_config: AppConfig) -> None:
        lock = FileLock(self._lock_file, timeout=5)
        try:
            with lock:
                data_to_dump = app_config.model_dump(mode="json")
                with open(self._config_path, "w", encoding="utf-8") as f:
                    yaml.dump(data_to_dump, f, allow_unicode=True, sort_keys=False)
        except TimeoutError:
            logger.error("无法获取配置文件")
            raise ServerRequestsError("写入配置文件失败，请稍后尝试")

    async def load_global(self) -> Optional[AppConfig]:
        return await self.load()

    async def load_user_override_payload(self, user_id: str) -> dict:
        return {}

    async def load_user_override(self, user_id: str) -> Optional[AppConfig]:
        return None

    async def load(self) -> Optional[AppConfig]:
        return await asyncio.to_thread(self._load_sync)

    async def save_global(
        self,
        app_config: AppConfig,
        *,
        changed_by: Optional[str] = None,
        note: str = "",
    ) -> None:
        await self.save(app_config)

    async def save_user_override(
        self,
        user_id: str,
        partial_config: AppConfig,
        *,
        changed_by: Optional[str] = None,
        note: str = "",
    ) -> None:
        raise ServerRequestsError("文件模式不支持用户级配置覆盖，请启用 USE_DB_APP_CONFIG")

    async def save_user_override_payload(
        self,
        user_id: str,
        payload: dict,
        *,
        changed_by: Optional[str] = None,
        note: str = "",
    ) -> None:
        raise ServerRequestsError("文件模式不支持用户级配置覆盖，请启用 USE_DB_APP_CONFIG")

    async def delete_user_override(self, user_id: str) -> None:
        raise ServerRequestsError("文件模式不支持用户级配置覆盖，请启用 USE_DB_APP_CONFIG")

    async def list_revisions(
        self,
        *,
        config_id: Optional[str] = None,
        scope: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[AppConfigRevision]:
        return []

    async def get_revision(self, revision_id: str) -> Optional[AppConfigRevision]:
        return None

    async def rollback_to_revision(
        self,
        revision_id: str,
        *,
        changed_by: Optional[str] = None,
    ) -> AppConfig:
        raise NotFoundError(f"配置版本[{revision_id}]不存在")

    async def save(self, app_config: AppConfig) -> None:
        await asyncio.to_thread(self._save_sync, app_config)
