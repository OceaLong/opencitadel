"""PostgreSQL adapter for fail-closed projection rebuilds."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.kernel.domain.events import StoredEvent, replay
from app.kernel.domain.types import OwnerScopeRef

from .models import KERNEL_PROJECTION_TABLES, KernelEventORM
from .projections import ProjectionRegistry


class PostgresProjectionRebuildStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        decrypt_private: Callable[[str], dict[str, object]],
        projections: ProjectionRegistry | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._decrypt_private = decrypt_private
        self._projections = projections or ProjectionRegistry()

    async def rebuild(self) -> None:
        async with self._session_factory() as session, session.begin():
            rows = (
                await session.scalars(
                    select(KernelEventORM).order_by(
                        KernelEventORM.run_id,
                        KernelEventORM.version,
                    )
                )
            ).all()
            streams: dict[object, list[StoredEvent]] = defaultdict(list)
            materialized: list[tuple[StoredEvent, dict[str, object]]] = []
            for row in rows:
                event = StoredEvent(
                    event_id=row.event_id,
                    run_id=row.run_id,
                    version=row.version,
                    type=row.event_type,
                    schema_version=row.schema_version,
                    public_payload=row.public_payload,
                    private_payload_ciphertext=row.private_payload_ciphertext,
                    previous_hash=row.previous_hash,
                    hash=row.hash,
                    owner_scope=OwnerScopeRef(
                        owner_user_id=row.owner_user_id,
                        team_id=row.team_id,
                    ),
                    actor_user_id=row.actor_user_id,
                    request_id=row.request_id,
                    causation_id=row.causation_id,
                    correlation_id=row.correlation_id,
                    occurred_at=row.occurred_at,
                )
                streams[event.run_id].append(event)
                materialized.append(
                    (event, self._decrypt_private(event.private_payload_ciphertext))
                )
            for events in streams.values():
                replay(tuple(events))
            for table in reversed(KERNEL_PROJECTION_TABLES):
                await session.execute(delete(table))
            for event, private_payload in materialized:
                await self._projections.apply(session, event, private_payload)
