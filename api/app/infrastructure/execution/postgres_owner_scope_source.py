"""Discover owner scopes whose formal projections lag the Event Store."""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.infrastructure.execution.models import (
    ExecutionEventORM,
    ExecutionProjectorCheckpointORM,
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
        owner_scope_key = func.concat(
            func.coalesce("user:" + ExecutionEventORM.owner_user_id, ""),
            func.coalesce("team:" + ExecutionEventORM.team_id, ""),
        )
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            rows = (
                await session.execute(
                    select(
                        ExecutionEventORM.owner_user_id,
                        ExecutionEventORM.team_id,
                        func.max(ExecutionEventORM.position).label("head"),
                        func.max(ExecutionProjectorCheckpointORM.last_position).label("checkpoint"),
                    )
                    .outerjoin(
                        ExecutionProjectorCheckpointORM,
                        and_(
                            ExecutionProjectorCheckpointORM.projector_name == "formal",
                            ExecutionProjectorCheckpointORM.owner_scope_key == owner_scope_key,
                        ),
                    )
                    .group_by(
                        ExecutionEventORM.owner_user_id,
                        ExecutionEventORM.team_id,
                    )
                    .having(
                        or_(
                            func.max(ExecutionProjectorCheckpointORM.last_position).is_(None),
                            func.max(ExecutionProjectorCheckpointORM.last_position)
                            < func.max(ExecutionEventORM.position),
                        )
                    )
                    .order_by(func.min(ExecutionEventORM.position))
                    .limit(limit)
                )
            ).all()
        scopes = []
        for owner_user_id, team_id, _head, _checkpoint in rows:
            if owner_user_id is not None:
                scopes.append(OwnerScope.personal(owner_user_id))
            elif team_id is not None:
                scopes.append(OwnerScope.team("execution-kernel", team_id))
            else:
                raise ValueError("execution event has no OwnerScope")
        return tuple(scopes)


__all__ = ["PostgresOwnerScopeSource"]
