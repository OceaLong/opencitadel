"""Retention purge for soft-deleted sessions and knowledge bases.

Soft-deleted rows enter the recycle bin (restorable, evidence-chain friendly)
but must not linger forever: storage-limitation compliance requires an upper
bound on retention. This service physically purges rows whose ``deleted_at``
passed the configured retention window; the scheduler leader tick drives it,
batched so no single tick holds a long transaction. Every automatic purge is
audited so deletion stays traceable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from app.application.services.audit_service import AuditService
from app.domain.models.audit_log import AuditLog
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class RecycleBinRetentionService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        *,
        retention_days: int,
        batch_size: int = 100,
        audit_service: AuditService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._retention_days = retention_days
        self._batch_size = batch_size
        self._audit_service = audit_service

    @property
    def enabled(self) -> bool:
        return self._retention_days > 0

    async def purge_expired(self, *, now: datetime) -> dict[str, int]:
        """Purge one batch of expired recycle-bin rows; returns per-kind counts."""
        if not self.enabled:
            return {"sessions": 0, "knowledge_bases": 0}
        cutoff = now - timedelta(days=self._retention_days)
        purged_sessions: list[str] = []
        purged_kbs: list[str] = []
        async with self._uow_factory() as uow:
            expired_sessions = await uow.session.list_deleted_before(cutoff, limit=self._batch_size)
            for session_id in expired_sessions:
                if await uow.session.purge(session_id):
                    purged_sessions.append(session_id)  # noqa: PERF401
            expired_kbs = await uow.knowledge_base.list_deleted_kbs_before(
                cutoff, limit=self._batch_size
            )
            for kb_id in expired_kbs:
                if await uow.knowledge_base.purge_kb(kb_id):
                    purged_kbs.append(kb_id)  # noqa: PERF401
            await uow.commit()
        await self._audit("session", purged_sessions)
        await self._audit("knowledge_base", purged_kbs)
        return {"sessions": len(purged_sessions), "knowledge_bases": len(purged_kbs)}

    async def _audit(self, resource_type: str, resource_ids: list[str]) -> None:
        if self._audit_service is None:
            return
        for resource_id in resource_ids:
            try:
                await self._audit_service.record(
                    AuditLog(
                        actor_user_id=None,
                        action="recycle_bin.auto_purge",
                        resource_type=resource_type,
                        resource_id=resource_id,
                        metadata={"retention_days": str(self._retention_days)},
                    )
                )
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning(
                    "回收站自动清理审计记录失败 %s=%s: %s", resource_type, resource_id, exc
                )


__all__ = ["RecycleBinRetentionService"]
