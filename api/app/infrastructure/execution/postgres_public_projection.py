"""Owner-scoped queries over the formal public execution-event projection."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.execution.public_projection import (
    PublicEventCursor,
    PublicEventPage,
    PublicExecutionEvent,
)
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.infrastructure.execution.models import ExecutionPublicEventORM
from app.infrastructure.security.db_authorization import configure_session_authorization


class PostgresPublicProjection:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext | None,
        cursor: PublicEventCursor,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization
        self._cursor = cursor

    async def list_events(
        self,
        *,
        source_entity_type: str,
        source_entity_id: str,
        owner_scope: OwnerScope,
        run_id: UUID | None = None,
        after: str | None = None,
        before: str | None = None,
        latest: bool = False,
        limit: int = 100,
    ) -> PublicEventPage:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if sum((after is not None, before is not None, latest)) > 1:
            raise ValueError("after, before and latest are mutually exclusive")
        after_position = self._cursor.decode(after) if after else None
        before_position = self._cursor.decode(before) if before else None
        scope_filter = self._scope_filter(owner_scope)
        statement = select(ExecutionPublicEventORM).where(
            ExecutionPublicEventORM.source_entity_type == source_entity_type,
            ExecutionPublicEventORM.source_entity_id == source_entity_id,
            scope_filter,
        )
        if run_id is not None:
            statement = statement.where(ExecutionPublicEventORM.run_id == run_id)
        reverse = latest or before_position is not None
        if after_position is not None:
            statement = statement.where(ExecutionPublicEventORM.position > after_position)
        if before_position is not None:
            statement = statement.where(ExecutionPublicEventORM.position < before_position)
        order = (
            ExecutionPublicEventORM.position.desc()
            if reverse
            else ExecutionPublicEventORM.position.asc()
        )
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            rows = list((await session.scalars(statement.order_by(order).limit(limit))).all())
            if reverse:
                rows.reverse()
            has_earlier = False
            if rows:
                has_earlier = (
                    await session.scalar(
                        select(ExecutionPublicEventORM.position)
                        .where(
                            ExecutionPublicEventORM.source_entity_type == source_entity_type,
                            ExecutionPublicEventORM.source_entity_id == source_entity_id,
                            scope_filter,
                            *(
                                (ExecutionPublicEventORM.run_id == run_id,)
                                if run_id is not None
                                else ()
                            ),
                            ExecutionPublicEventORM.position < rows[0].position,
                        )
                        .limit(1)
                    )
                ) is not None
        events = tuple(
            PublicExecutionEvent(
                cursor=(cursor := self._cursor.encode(row.position)),
                event_id=row.event_id,
                event_type=row.event_type,
                run_id=row.run_id,
                stream_id=row.stream_id,
                stream_version=row.stream_version,
                payload={**row.payload, "event_id": cursor},
                occurred_at=row.occurred_at,
            )
            for row in rows
        )
        return PublicEventPage(
            events=events,
            next_cursor=(events[-1].cursor if len(events) == limit and not reverse else None),
            prev_cursor=events[0].cursor if events else None,
            has_earlier=has_earlier,
        )

    @staticmethod
    def _scope_filter(owner_scope: OwnerScope):
        if owner_scope.type == OwnerScopeType.PERSONAL:
            if owner_scope.team_id is not None:
                raise ValueError("personal scope cannot include team_id")
            return ExecutionPublicEventORM.owner_user_id == owner_scope.user_id
        if owner_scope.type == OwnerScopeType.TEAM and owner_scope.team_id:
            return ExecutionPublicEventORM.team_id == owner_scope.team_id
        raise ValueError("team scope requires team_id")


__all__ = ["PostgresPublicProjection"]
