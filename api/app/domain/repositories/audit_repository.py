#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional

from app.domain.models.audit_log import AuditLog


class AuditRepository(ABC):
    @abstractmethod
    async def add(self, log: AuditLog) -> None:
        ...

    @abstractmethod
    async def list(
        self,
        *,
        actor_user_id: Optional[str] = None,
        action: Optional[str] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        resource_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[AuditLog]:
        ...

    @abstractmethod
    async def get_by_id(self, log_id: str) -> Optional[AuditLog]:
        ...

    @abstractmethod
    async def count(
        self,
        *,
        actor_user_id: Optional[str] = None,
        action: Optional[str] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        resource_id: Optional[str] = None,
        resource_type: Optional[str] = None,
    ) -> int:
        ...

    async def list_chained(
        self,
        *,
        limit: Optional[int] = None,
        resource_id: Optional[str] = None,
    ) -> List[AuditLog]:
        raise NotImplementedError
