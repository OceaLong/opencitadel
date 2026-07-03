#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Protocol, Optional, List, Dict, Any

from app.domain.models.app_config import AppConfig
from app.domain.models.app_config_revision import AppConfigRevision


class AppConfigRepository(Protocol):
    async def load_global(self) -> Optional[AppConfig]:
        ...

    async def load_user_override(self, user_id: str) -> Optional[AppConfig]:
        ...

    async def load_user_override_payload(self, user_id: str) -> Dict[str, Any]:
        ...

    async def save_global(self, app_config: AppConfig, *, changed_by: Optional[str] = None, note: str = "") -> None:
        ...

    async def save_user_override(
        self,
        user_id: str,
        partial_config: AppConfig,
        *,
        changed_by: Optional[str] = None,
        note: str = "",
    ) -> None:
        ...

    async def save_user_override_payload(
        self,
        user_id: str,
        payload: Dict[str, Any],
        *,
        changed_by: Optional[str] = None,
        note: str = "",
    ) -> None:
        ...

    async def delete_user_override(self, user_id: str) -> None:
        ...

    async def list_revisions(
        self,
        *,
        config_id: Optional[str] = None,
        scope: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[AppConfigRevision]:
        ...

    async def get_revision(self, revision_id: str) -> Optional[AppConfigRevision]:
        ...

    async def rollback_to_revision(self, revision_id: str, *, changed_by: Optional[str] = None) -> AppConfig:
        ...

    # Backward-compatible aliases
    async def load(self) -> Optional[AppConfig]:
        ...

    async def save(self, app_config: AppConfig) -> None:
        ...
