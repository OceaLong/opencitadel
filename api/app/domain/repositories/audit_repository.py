#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

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

    @abstractmethod
    async def count_by_actions(
        self,
        actions: List[str],
        *,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
    ) -> int:
        """Count audit logs whose action is in the given set (e.g. login-related actions)."""
        ...

    @abstractmethod
    async def count_by_action_prefix(
        self,
        prefix: str,
        *,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
    ) -> int:
        """Count audit logs whose action starts with the given prefix (e.g. 'admin.')."""
        ...

    @abstractmethod
    async def list_recent_chained(self, limit: int = 20) -> List[AuditLog]:
        """The most recent ``limit`` chained entries, ordered ascending by
        ``chain_seq`` (oldest of the batch first).

        ``chain_seq`` is assigned once at write time in insertion order and
        never revisited -- it is independent of ``created_at``. Ordering by
        it (rather than by ``created_at`` itself) is what makes a downstream
        "is created_at monotonic along this sample" check meaningful instead
        of tautological: sorting by created_at and then checking it is
        sorted would always succeed by construction.
        """
        ...

    async def list_chained(
        self,
        *,
        limit: Optional[int] = None,
        resource_id: Optional[str] = None,
    ) -> List[AuditLog]:
        raise NotImplementedError

    @abstractmethod
    async def daily_action_counts(
        self,
        actions: List[str],
        *,
        since: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Per-day, per-action counts for the given actions.

        Returns one row per ``(date, action)`` pair that has at least one
        matching log, e.g. ``[{"date": "2026-08-01", "action": "x", "count": 3}]``.
        Callers that need a single combined daily series (e.g. summing several
        actions together) do that summation themselves -- this method stays a
        thin, reusable "count grouped by day and action" primitive, mirroring
        ``count_by_actions``/``count_by_action_prefix`` above. An empty
        ``actions`` list returns ``[]`` without querying.
        """
        ...
