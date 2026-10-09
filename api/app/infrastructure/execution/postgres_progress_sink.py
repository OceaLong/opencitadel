"""Postgres sink for off-stream activity progress telemetry."""

from __future__ import annotations

import logging

from sqlalchemy import case, func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.execution.progress import ActivityProgressRecord
from app.application.execution.view_facts import safe_text
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import (
    ExecutionPublicEventORM,
    ExecutionResourceBuildProjectionORM,
)
from app.infrastructure.execution.postgres_view_observations import observe_progress
from app.infrastructure.security.db_authorization import configure_session_authorization

logger = logging.getLogger(__name__)

_TERMINAL_BUILD_STATUSES = ("completed", "failed", "cancelled")


class PostgresActivityProgressSink:
    """Write one progress report to the public feed and the build projection.

    Both writes are idempotent (deterministic event_id / monotonic progress),
    so at-least-once reporting from the activity worker is safe. A failure is
    reported back as False — progress is telemetry and must never fail the
    activity that produced it.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization

    async def record(self, record: ActivityProgressRecord) -> bool:
        payload = {
            "event_id": str(record.event_id),
            "created_at": int(record.occurred_at.timestamp()),
            "schema_version": 1,
            "visibility": "user",
            "channel": "ui",
            "persist": True,
            "activity_id": str(record.activity_id),
            "kind": record.kind,
            "phase": safe_text(record.phase, 64),
            "status": record.status,
            "progress": record.progress,
            "message": safe_text(record.message),
        }
        try:
            async with self._session_factory() as session:
                await configure_session_authorization(session, self._authorization)
                observation = await observe_progress(session, record)
                if not observation.new_source:
                    await session.commit()
                    return True
                await session.execute(
                    pg_insert(ExecutionPublicEventORM)
                    .values(
                        position=None,
                        event_id=record.event_id,
                        run_id=record.run_id,
                        source_entity_type=record.source_entity_type,
                        source_entity_id=record.source_entity_id,
                        stream_type="run",
                        stream_id=str(record.run_id),
                        stream_version=0,
                        event_type="resource_build",
                        payload=payload,
                        owner_user_id=record.owner_user_id,
                        team_id=record.team_id,
                        occurred_at=record.occurred_at,
                    )
                    .on_conflict_do_nothing(index_elements=["event_id"])
                )
                if observation.public_payload.get("applied", True):
                    await self._update_build(session, record)
                await session.commit()
            return True
        except (SQLAlchemyError, OSError, RuntimeError, ValueError) as exc:
            logger.warning(
                "活动进度遥测写入失败 run=%s activity=%s seq=%s: %s",
                record.run_id,
                record.activity_id,
                record.sequence,
                exc,
            )
            return False

    @staticmethod
    async def _update_build(session, record):
        await session.execute(
            update(ExecutionResourceBuildProjectionORM)
            .where(
                ExecutionResourceBuildProjectionORM.run_id == record.run_id,
                ExecutionResourceBuildProjectionORM.status.not_in(_TERMINAL_BUILD_STATUSES),
            )
            .values(
                progress=func.greatest(
                    ExecutionResourceBuildProjectionORM.progress,
                    record.progress,
                ),
                phase=(
                    safe_text(record.phase, 64)
                    if record.phase is not None
                    else ExecutionResourceBuildProjectionORM.phase
                ),
                updated_at=case(
                    (
                        ExecutionResourceBuildProjectionORM.updated_at < record.occurred_at,
                        record.occurred_at,
                    ),
                    else_=ExecutionResourceBuildProjectionORM.updated_at,
                ),
            )
        )


__all__ = ["PostgresActivityProgressSink"]
