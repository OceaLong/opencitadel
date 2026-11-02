"""PostgreSQL lease fencing for durable execution Activities."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import and_, cast, delete, func, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.execution.activity import (
    ActivityClaim,
    ActivityLeaseConfirmation,
    ActivityRequest,
)
from app.domain.execution.commands import CommandEnvelope, normalize_utc
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import (
    ExecutionActivityTaskORM,
    ExecutionRunProjectionORM,
)
from app.infrastructure.execution.postgres_inbox import PostgresInbox
from app.infrastructure.security.db_authorization import configure_session_authorization

DEFAULT_MAX_CLAIM_ATTEMPTS = 5


class PostgresActivityStore:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
        max_claim_attempts: int = DEFAULT_MAX_CLAIM_ATTEMPTS,
        supported_implementations: frozenset[tuple[str, str, str]] | None = None,
    ) -> None:
        if max_claim_attempts <= 0:
            raise ValueError("max_claim_attempts must be positive")
        self._session_factory = session_factory
        self._authorization = authorization
        self._max_claim_attempts = max_claim_attempts
        self._supported_implementations = supported_implementations

    async def claim(
        self,
        *,
        now: datetime,
        limit: int,
        worker_id: str,
        claim_ttl: timedelta,
    ) -> tuple[ActivityClaim, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        # Caller time is useful to test callers, never a lease authority.
        normalize_utc(now)
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            resolved_now = await self._server_now(session)
            records = tuple(
                (
                    await session.scalars(
                        select(ExecutionActivityTaskORM)
                        .where(
                            tuple_(
                                ExecutionActivityTaskORM.definition_digest,
                                ExecutionActivityTaskORM.activity_type,
                                ExecutionActivityTaskORM.handler_version,
                            ).in_(sorted(self._supported_implementations))
                            if self._supported_implementations is not None
                            else True,
                            ExecutionActivityTaskORM.available_at <= resolved_now,
                            or_(
                                ExecutionActivityTaskORM.status == "pending",
                                and_(
                                    ExecutionActivityTaskORM.status.in_(
                                        ("claimed", "call_started")
                                    ),
                                    ExecutionActivityTaskORM.claim_deadline <= resolved_now,
                                ),
                            ),
                        )
                        .order_by(
                            ExecutionActivityTaskORM.available_at,
                            ExecutionActivityTaskORM.created_at,
                            ExecutionActivityTaskORM.activity_id,
                        )
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            claims: list[ActivityClaim] = []
            for record in records:
                # clock_timestamp(), unlike transaction now(), advances while
                # waiting for locks or processing the preceding rows.
                resolved_now = await self._server_now(session)
                # Poison-pill cap (K2-2/D5): a row that keeps getting claimed
                # without ever settling (worker crashes, stable infrastructure
                # fault, endless defer loop) is parked as dead_lettered instead
                # of being reclaimed forever. A FailActivity command is written
                # into the durable inbox in this same transaction (the timer
                # dispatcher's pattern) so the Run converges on the next kernel
                # tick instead of waiting out the activity-timeout timer — the
                # timer stays as the crash backstop and settles idempotently if
                # it fires anyway.
                # A possibly-sent operation must instead reach its recovery
                # classifier (or trusted timeout); a counter is not evidence
                # that an external side effect failed.
                record.claim_attempts += 1
                if (
                    record.claim_attempts > self._max_claim_attempts
                    and record.call_started_at is None
                ):
                    record.status = "dead_lettered"
                    record.failure_code = "ACTIVITY_DEAD_LETTERED"
                    record.completed_at = resolved_now
                    record.claimed_by = None
                    record.claim_deadline = None
                    record.heartbeat_at = None
                    record.updated_at = resolved_now
                    await PostgresInbox(session).receive(
                        self._dead_letter_command(record, now=resolved_now)
                    )
                    continue
                recovered_after_call_started = record.call_started_at is not None
                record.status = "claimed"
                record.claim_generation += 1
                record.claimed_by = worker_id
                record.claim_deadline = resolved_now + claim_ttl
                record.heartbeat_at = resolved_now
                record.updated_at = resolved_now
                claims.append(
                    ActivityClaim(
                        request=self._request(record),
                        claim_generation=record.claim_generation,
                        worker_id=worker_id,
                        server_now=resolved_now,
                        lease_deadline=record.claim_deadline,
                        owner_user_id=record.owner_user_id,
                        team_id=record.team_id,
                        recovered_after_call_started=recovered_after_call_started,
                    )
                )
            await session.commit()
            return tuple(claims)

    async def mark_call_started(
        self,
        claim: ActivityClaim,
        *,
        now: datetime,
    ) -> bool:
        normalize_utc(now)
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            record, resolved_now = await self._owned_task(session, claim)
            if record is None or record.status != "claimed" or record.timeout_at <= resolved_now:
                return False
            record.status = "call_started"
            record.attempt += 1
            record.call_started_at = resolved_now
            record.heartbeat_at = resolved_now
            record.updated_at = resolved_now
            await session.commit()
            return True

    async def heartbeat(
        self,
        claim: ActivityClaim,
        *,
        now: datetime,
        claim_ttl: timedelta,
    ) -> ActivityLeaseConfirmation | None:
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        normalize_utc(now)
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            record, resolved_now = await self._owned_task(session, claim)
            if record is None:
                return None
            record.heartbeat_at = resolved_now
            record.claim_deadline = resolved_now + claim_ttl
            record.updated_at = resolved_now
            confirmation = ActivityLeaseConfirmation(
                server_now=resolved_now, lease_deadline=record.claim_deadline
            )
            await session.commit()
            return confirmation

    async def defer(
        self,
        claim: ActivityClaim,
        *,
        now: datetime,
        retry_after: timedelta,
        consume_attempt: bool = False,
    ) -> bool:
        if retry_after <= timedelta(0):
            raise ValueError("retry_after must be positive")
        normalize_utc(now)
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            record, resolved_now = await self._owned_task(session, claim)
            if record is None:
                return False
            record.status = "pending"
            record.available_at = resolved_now + retry_after
            record.claimed_by = None
            record.claim_deadline = None
            record.heartbeat_at = None
            record.updated_at = resolved_now
            if not consume_attempt:
                # Admission waits and transient infrastructure deferrals are
                # not poison-pill failures. Never erase call_started evidence.
                record.claim_attempts = max(0, record.claim_attempts - 1)
            await session.commit()
            return True

    @staticmethod
    async def _server_now(session: AsyncSession) -> datetime:
        value = await session.scalar(select(func.clock_timestamp()))
        assert isinstance(value, datetime)
        return normalize_utc(value)

    @classmethod
    async def _owned_task(
        cls, session: AsyncSession, claim: ActivityClaim
    ) -> tuple[ExecutionActivityTaskORM | None, datetime]:
        record = await session.scalar(
            select(ExecutionActivityTaskORM)
            .where(
                ExecutionActivityTaskORM.activity_id == claim.request.activity_id,
                ExecutionActivityTaskORM.request_generation == claim.request.generation,
                ExecutionActivityTaskORM.claim_generation == claim.claim_generation,
                ExecutionActivityTaskORM.claimed_by == claim.worker_id,
                ExecutionActivityTaskORM.owner_user_id == claim.owner_user_id,
                ExecutionActivityTaskORM.team_id == claim.team_id,
                ExecutionActivityTaskORM.status.in_(("claimed", "call_started")),
            )
            .with_for_update()
        )
        resolved_now = await cls._server_now(session)
        if (
            record is None
            or record.claim_deadline is None
            or record.claim_deadline <= resolved_now
            or record.timeout_at <= resolved_now
        ):
            return None, resolved_now
        return record, resolved_now

    async def purge_completed(self, *, before: datetime, limit: int) -> int:
        """Delete a batch of terminal activity rows older than ``before``.

        Hard constraint: a row is only purgeable once its owning Run projection
        is terminal. An active Run's decision source rehydrates succeeded
        activities' ``decision_payload`` from these rows — purging them early
        would break digest verification and quarantine the Run. Rows without a
        run_id (non-run aggregates) are never purged here.
        Cancellation/abandonment does not settle an external effect: its owning
        Run's source rows remain recovery roots until the result is known.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")
        resolved_before = normalize_utc(before)
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            terminal_runs = select(ExecutionRunProjectionORM.run_id).where(
                ExecutionRunProjectionORM.terminal.is_(True),
                text("""
NOT EXISTS (
 SELECT 1 FROM jsonb_array_elements(
   COALESCE(execution_run_projection.state->'effect_operations','[]'::jsonb)
 ) effect
 WHERE COALESCE(effect->>'state','unknown') NOT IN ('applied','not_applied')
   OR effect->>'awaiting_result'='true' OR effect->>'integrity_conflict'='true'
)
AND NOT EXISTS (
 SELECT 1 FROM execution_effect_requests request
 WHERE request.run_id=execution_run_projection.run_id AND request.status IN ('pending','running')
)
"""),
            )
            purgeable = (
                select(ExecutionActivityTaskORM.activity_id)
                .where(
                    ExecutionActivityTaskORM.status.in_(
                        ("succeeded", "failed", "unknown", "cancelled", "dead_lettered")
                    ),
                    ExecutionActivityTaskORM.updated_at < resolved_before,
                    ExecutionActivityTaskORM.run_id.is_not(None),
                    cast(ExecutionActivityTaskORM.run_id, PGUUID).in_(terminal_runs),
                )
                .limit(limit)
            )
            result = await session.execute(
                delete(ExecutionActivityTaskORM).where(
                    ExecutionActivityTaskORM.activity_id.in_(purgeable)
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

    @staticmethod
    def _dead_letter_command(
        record: ExecutionActivityTaskORM,
        *,
        now: datetime,
    ) -> CommandEnvelope:
        """FailActivity that settles a dead-lettered task on the aggregate.

        Deterministic command_id per (activity, generation): redelivery
        deduplicates in the inbox, and a later ACTIVITY_TIMEOUT settlement of
        the same activity is absorbed idempotently by the aggregate.
        """
        return CommandEnvelope(
            command_id=uuid5(
                NAMESPACE_URL,
                "opencitadel:activity-dead-letter:"
                f"{record.activity_id}:{record.request_generation}",
            ),
            command_type="FailActivity",
            command_schema_version=1,
            stream_type=record.aggregate_type,
            stream_id=record.aggregate_id,
            expected_stream_version=None,
            owner_user_id=record.owner_user_id,
            team_id=record.team_id,
            correlation_id=record.activity_id,
            causation_id=record.activity_id,
            issued_at=now,
            payload={
                "activity_id": str(record.activity_id),
                "generation": record.request_generation,
                "failure_code": "ACTIVITY_DEAD_LETTERED",
            },
        )

    @staticmethod
    def _request(record: ExecutionActivityTaskORM) -> ActivityRequest:
        return ActivityRequest(
            activity_id=record.activity_id,
            activity_type=record.activity_type,
            aggregate_type=record.aggregate_type,
            aggregate_id=record.aggregate_id,
            generation=record.request_generation,
            timeout_at=record.timeout_at,
            input_ref=record.request_ref,
            input_digest=record.request_digest,
            input_payload=record.request_payload,
            definition_digest=record.definition_digest,
            handler_version=record.handler_version,
        )


__all__ = ["DEFAULT_MAX_CLAIM_ATTEMPTS", "PostgresActivityStore"]
