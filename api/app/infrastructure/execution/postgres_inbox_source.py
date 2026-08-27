"""Read pending durable Command envelopes for worker delivery."""

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.execution.commands import CommandEnvelope
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import ExecutionCommandInboxORM
from app.infrastructure.security.db_authorization import configure_session_authorization


class PostgresInboxSource:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization

    async def load_pending(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[CommandEnvelope, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            rows = tuple(
                (
                    await session.scalars(
                        select(ExecutionCommandInboxORM)
                        .where(
                            or_(
                                ExecutionCommandInboxORM.status == "received",
                                (ExecutionCommandInboxORM.status == "processing")
                                & (ExecutionCommandInboxORM.claim_deadline < now),
                            )
                        )
                        .order_by(ExecutionCommandInboxORM.received_at)
                        .limit(limit)
                    )
                ).all()
            )
        return tuple(self._to_command(row) for row in rows)

    @staticmethod
    def _to_command(row: ExecutionCommandInboxORM) -> CommandEnvelope:
        return CommandEnvelope(
            command_id=row.command_id,
            command_type=row.command_type,
            command_schema_version=row.command_schema_version,
            stream_type=row.stream_type,
            stream_id=row.stream_id,
            expected_stream_version=row.expected_stream_version,
            owner_user_id=row.owner_user_id,
            team_id=row.team_id,
            correlation_id=row.correlation_id,
            causation_id=row.causation_id,
            issued_at=row.issued_at,
            payload=row.payload,
            payload_digest=row.payload_digest,
        )


__all__ = ["PostgresInboxSource"]
