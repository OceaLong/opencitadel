"""Discover owner scopes whose formal projections lag the Event Store."""

from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.infrastructure.execution.models import (
    ExecutionPoisonedScopeORM,
    ExecutionProjectorCheckpointORM,
    ExecutionScopeHeadORM,
)
from app.infrastructure.observability.execution_metrics import record_poisoned_scope
from app.infrastructure.security.db_authorization import configure_session_authorization


class PostgresOwnerScopeSource:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization

    async def list_pending(self, *, limit: int) -> tuple[OwnerScope, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        # C3b: the head watermark is maintained incrementally by the append path
        # (execution_scope_head, one row per owner scope). Discovering lagging
        # scopes is therefore a bounded index scan of head rows outer-joined to
        # the formal checkpoint — no ``GROUP BY ... max(position)`` full-table
        # aggregate over the ever-growing execution_events table on every poll.
        #
        # Quarantined / rebuilding scopes (any execution_poisoned_scopes row,
        # K4-1) are excluded so a poison scope is skipped on every subsequent
        # scan instead of re-failing the pass forever and starving its peers.
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            rows = (
                await session.execute(
                    select(ExecutionScopeHeadORM.owner_scope_key)
                    .outerjoin(
                        ExecutionProjectorCheckpointORM,
                        and_(
                            ExecutionProjectorCheckpointORM.projector_name == "formal",
                            ExecutionProjectorCheckpointORM.owner_scope_key
                            == ExecutionScopeHeadORM.owner_scope_key,
                        ),
                    )
                    .where(
                        ExecutionScopeHeadORM.head_position
                        > func.coalesce(ExecutionProjectorCheckpointORM.last_position, -1),
                        ExecutionScopeHeadORM.owner_scope_key.not_in(
                            select(ExecutionPoisonedScopeORM.owner_scope_key)
                        ),
                    )
                    .order_by(ExecutionScopeHeadORM.head_position.asc())
                    .limit(limit)
                )
            ).all()
        return tuple(self._scope_from_key(owner_scope_key) for (owner_scope_key,) in rows)

    async def quarantine(
        self,
        owner_scope: OwnerScope,
        *,
        reason: str,
        error: str,
        failure_count: int,
    ) -> None:
        """Durably exclude one owner scope from pending discovery (D12/K4-1)."""
        key, owner_user_id, team_id = self._scope_parts(owner_scope)
        now = datetime.now(UTC)
        detail = error[:2000] or reason
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            await session.execute(
                pg_insert(ExecutionPoisonedScopeORM)
                .values(
                    owner_scope_key=key,
                    owner_user_id=owner_user_id,
                    team_id=team_id,
                    reason=reason[:128],
                    last_error=detail,
                    failure_count=failure_count,
                    rebuilding=False,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["owner_scope_key"],
                    set_={
                        "reason": reason[:128],
                        "last_error": detail,
                        "failure_count": ExecutionPoisonedScopeORM.failure_count + 1,
                        "last_seen_at": now,
                    },
                )
            )
            await session.commit()
        record_poisoned_scope()

    @staticmethod
    def _scope_parts(
        owner_scope: OwnerScope,
    ) -> tuple[str, str | None, str | None]:
        if owner_scope.type == OwnerScopeType.PERSONAL:
            return f"user:{owner_scope.user_id}", owner_scope.user_id, None
        if owner_scope.type == OwnerScopeType.TEAM and owner_scope.team_id:
            return f"team:{owner_scope.team_id}", None, owner_scope.team_id
        raise ValueError("owner scope requires user_id or team_id")

    @staticmethod
    def _scope_from_key(owner_scope_key: str) -> OwnerScope:
        prefix, _, value = owner_scope_key.partition(":")
        if prefix == "user" and value:
            return OwnerScope.personal(value)
        if prefix == "team" and value:
            return OwnerScope.team("execution-kernel", value)
        raise ValueError("execution scope head has no OwnerScope")


__all__ = ["PostgresOwnerScopeSource"]
