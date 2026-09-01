"""Read pending durable Command envelopes for worker delivery."""

from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.execution.commands import CommandEnvelope, normalize_utc
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import ExecutionCommandInboxORM
from app.infrastructure.security.db_authorization import configure_session_authorization


class PostgresInboxSource:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
        claim_ttl: timedelta = timedelta(seconds=30),
    ) -> None:
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        self._session_factory = session_factory
        self._authorization = authorization
        self._claim_ttl = claim_ttl

    async def load_pending(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[CommandEnvelope, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        resolved_now = normalize_utc(now)
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            # SKIP LOCKED lets horizontally scaled kernel replicas claim disjoint
            # batches without blocking each other on row locks. Rows a peer already
            # locked (or freshly claimed) are skipped instead of contended.
            rows = tuple(
                (
                    await session.scalars(
                        select(ExecutionCommandInboxORM)
                        .where(
                            or_(
                                ExecutionCommandInboxORM.status == "received",
                                (ExecutionCommandInboxORM.status == "processing")
                                & (ExecutionCommandInboxORM.claim_deadline < resolved_now),
                            )
                        )
                        .order_by(ExecutionCommandInboxORM.received_at)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            # Claim the batch in the same transaction: mark each row ``processing``
            # with a fresh lease so peers neither re-load nor re-process it. The
            # lease deadline still governs crash recovery (an unfinished claim is
            # re-eligible once ``claim_deadline`` elapses).
            for row in rows:
                row.status = "processing"
                row.claim_generation += 1
                row.processing_started_at = resolved_now
                row.claim_deadline = resolved_now + self._claim_ttl
            # Build envelopes before COMMIT because the production session factory
            # expires attributes on commit (no lazy async reload allowed here).
            commands = tuple(self._to_command(row) for row in rows)
            await session.commit()
        return commands

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
