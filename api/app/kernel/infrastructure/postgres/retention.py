"""Idempotent retention candidates derived from archived Run projections."""

from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.authorization import AuthorizationContext
from app.kernel.application.retention_worker import RetentionCandidate
from app.kernel.domain.types import OwnerScopeRef, Workflow

from .models import KernelRunViewORM
from .session_auth import bind_context


class PostgresRetentionStore:
    """Return due rows without mutable lease state; command IDs provide fencing."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> tuple[RetentionCandidate, ...]:
        del worker_id, lease_seconds
        async with self._session_factory() as session:
            await bind_context(session, AuthorizationContext.system("retention-claim"))
            rows = (
                await session.scalars(
                    select(KernelRunViewORM)
                    .where(
                        KernelRunViewORM.status == "archived",
                        KernelRunViewORM.purge_after.is_not(None),
                        KernelRunViewORM.purge_after <= now,
                    )
                    .order_by(KernelRunViewORM.purge_after, KernelRunViewORM.id)
                    .limit(limit)
                )
            ).all()
        return tuple(self._candidate(row) for row in rows)

    async def mark_completed(
        self,
        candidate_id: UUID,
        claim_generation: int,
        *,
        now: datetime,
    ) -> bool:
        del candidate_id, claim_generation, now
        # PurgeRun is synchronously journaled and projected by CommandService.
        # A duplicate claimant uses the identical deterministic command id and
        # receives the existing terminal result, so no second lease table is needed.
        return True

    @staticmethod
    def _candidate(row: KernelRunViewORM) -> RetentionCandidate:
        generation = max(1, row.stream_version)
        candidate_id = uuid5(
            NAMESPACE_URL,
            f"kernel-retention:run:{row.id}:{generation}",
        )
        disposition_hash = hashlib.sha256(
            f"purge:run:{row.id}:{generation}:{row.purge_after.isoformat()}".encode()
        ).hexdigest()
        return RetentionCandidate(
            candidate_id=candidate_id,
            resource_type="run",
            resource_id=str(row.id),
            workflow=Workflow(row.workflow),
            owner_scope=OwnerScopeRef(
                owner_user_id=row.owner_user_id,
                team_id=row.team_id,
            ),
            disposition_hash=disposition_hash,
            claim_generation=generation,
        )


__all__ = ["PostgresRetentionStore"]
