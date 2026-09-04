"""Admin observability queries over the formal projection (D13/K4-3).

Read-only: per-scope projection lag (scope head watermark minus the formal
checkpoint) and the quarantined/rebuilding scope list. Served to the admin
status endpoint; the caller's request identity (admin) satisfies the RLS on
``execution_projector_checkpoints``, while the two control tables carry no
tenant RLS and are granted SELECT to the API role.
"""

from __future__ import annotations

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.queries import PoisonedScopeEntry, ProjectionScopeLag
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import (
    ExecutionPoisonedScopeORM,
    ExecutionProjectorCheckpointORM,
    ExecutionScopeHeadORM,
)
from app.infrastructure.security.db_authorization import configure_session_authorization


class PostgresProjectionStatusQuery:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext | None,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization

    async def scope_lags(self, *, limit: int = 100) -> tuple[ProjectionScopeLag, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        checkpoint_position = func.coalesce(ExecutionProjectorCheckpointORM.last_position, 0)
        lag = ExecutionScopeHeadORM.head_position - checkpoint_position
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            rows = (
                await session.execute(
                    select(
                        ExecutionScopeHeadORM.owner_scope_key,
                        ExecutionScopeHeadORM.head_position,
                        checkpoint_position,
                        lag,
                    )
                    .outerjoin(
                        ExecutionProjectorCheckpointORM,
                        and_(
                            ExecutionProjectorCheckpointORM.projector_name == "formal",
                            ExecutionProjectorCheckpointORM.owner_scope_key
                            == ExecutionScopeHeadORM.owner_scope_key,
                        ),
                    )
                    .where(lag > 0)
                    .order_by(lag.desc())
                    .limit(limit)
                )
            ).all()
        return tuple(
            ProjectionScopeLag(
                owner_scope_key=key,
                head_position=int(head),
                checkpoint_position=int(checkpoint),
                lag=int(gap),
            )
            for key, head, checkpoint, gap in rows
        )

    async def poisoned_scopes(self) -> tuple[PoisonedScopeEntry, ...]:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            rows = (
                await session.scalars(
                    select(ExecutionPoisonedScopeORM).order_by(
                        ExecutionPoisonedScopeORM.last_seen_at.desc()
                    )
                )
            ).all()
        return tuple(
            PoisonedScopeEntry(
                owner_scope_key=row.owner_scope_key,
                reason=row.reason,
                last_error=row.last_error,
                failure_count=row.failure_count,
                rebuilding=row.rebuilding,
                first_seen_at=row.first_seen_at,
                last_seen_at=row.last_seen_at,
            )
            for row in rows
        )


__all__ = ["PostgresProjectionStatusQuery"]
