"""Admin observability queries over the formal projection (D13/K4-3).

Read-only: per-scope projection lag (scope head watermark minus the formal
checkpoint) and the quarantined/rebuilding scope list. Served to the admin
status endpoint; the caller's request identity (admin) satisfies the RLS on
``execution_projector_checkpoints``, while the two control tables carry no
tenant RLS and are granted SELECT to the API role.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.queries import PoisonedScopeEntry, ProjectionScopeLag
from app.domain.errors import NotFoundError
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import (
    ExecutionPoisonedRunORM,
    ExecutionPoisonedScopeORM,
    ExecutionProjectorCheckpointORM,
    ExecutionRecoveryRequestORM,
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

    async def poisoned_runs(self) -> list[dict]:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            rows = (
                await session.scalars(
                    select(ExecutionPoisonedRunORM)
                    .order_by(ExecutionPoisonedRunORM.last_seen_at.desc())
                    .limit(1000)
                )
            ).all()
            return [
                {
                    "run_id": str(row.run_id),
                    "owner_scope_key": (
                        f"team:{row.team_id}" if row.team_id else f"user:{row.owner_user_id}"
                    ),
                    "reason": row.reason,
                    "last_error": row.last_error,
                    "failure_count": row.failure_count,
                    "next_attempt_at": row.next_attempt_at.isoformat()
                    if row.next_attempt_at
                    else None,
                    "last_seen_at": row.last_seen_at.isoformat(),
                }
                for row in rows
            ]

    async def recovery_requests(self) -> list[dict]:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            rows = (
                await session.scalars(
                    select(ExecutionRecoveryRequestORM)
                    .order_by(ExecutionRecoveryRequestORM.created_at.desc())
                    .limit(100)
                )
            ).all()
            return [
                {
                    "id": str(row.id),
                    "owner_scope_key": row.owner_scope_key,
                    "status": row.status,
                    "reason": row.reason,
                    "result": row.result,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    async def request_recovery(self, *, scope_key: str, actor: str, reason: str) -> str:
        prefix, _, identity = scope_key.partition(":")
        if prefix not in {"user", "team"} or not identity or not reason.strip():
            raise ValueError("Recovery requires an owner scope and a reason")
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"recovery:{scope_key}"},
            )
            existing = await session.scalar(
                select(ExecutionRecoveryRequestORM).where(
                    ExecutionRecoveryRequestORM.owner_scope_key == scope_key,
                    ExecutionRecoveryRequestORM.status == "pending",
                )
            )
            if existing is not None:
                return str(existing.id)
            if (
                await session.get(ExecutionScopeHeadORM, scope_key) is None
                and await session.get(ExecutionPoisonedScopeORM, scope_key) is None
            ):
                raise NotFoundError("Execution scope does not exist")
            row = ExecutionRecoveryRequestORM(
                id=uuid4(),
                owner_scope_key=scope_key,
                requested_by=actor,
                reason=reason.strip(),
                status="pending",
                result={},
                created_at=datetime.now(UTC),
            )
            session.add(row)
            await session.commit()
            return str(row.id)


__all__ = ["PostgresProjectionStatusQuery"]
