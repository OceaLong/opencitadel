"""Discover owner scopes whose formal projections lag the Event Store."""

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.infrastructure.execution.models import (
    ExecutionProjectorCheckpointORM,
    ExecutionScopeHeadORM,
)
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
                        > func.coalesce(ExecutionProjectorCheckpointORM.last_position, -1)
                    )
                    .order_by(ExecutionScopeHeadORM.head_position.asc())
                    .limit(limit)
                )
            ).all()
        return tuple(self._scope_from_key(owner_scope_key) for (owner_scope_key,) in rows)

    @staticmethod
    def _scope_from_key(owner_scope_key: str) -> OwnerScope:
        prefix, _, value = owner_scope_key.partition(":")
        if prefix == "user" and value:
            return OwnerScope.personal(value)
        if prefix == "team" and value:
            return OwnerScope.team("execution-kernel", value)
        raise ValueError("execution scope head has no OwnerScope")


__all__ = ["PostgresOwnerScopeSource"]
