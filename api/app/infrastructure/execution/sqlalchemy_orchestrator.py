"""SQLAlchemy transactional Command handler for the pure Aggregate runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.execution import CommandResult
from app.domain.execution.aggregate import (
    Aggregate,
    Decision,
    ReplayResult,
    ReplaySnapshot,
    replay,
)
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.errors import CommandInProgressError, RejectionCode
from app.domain.execution.run import (
    ExpectedStreamVersionError,
    InvalidRunTransitionError,
    UnknownRunCommandError,
)
from app.domain.execution.store import (
    AppendContext,
    CorruptEventStreamError,
    EventStore,
    OptimisticConcurrencyError,
    PayloadTooLargeError,
    StreamRef,
)
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import (
    ExecutionActivityTaskORM,
    ExecutionOutboxORM,
    ExecutionScheduledCommandORM,
)
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.execution.postgres_inbox import PostgresInbox
from app.infrastructure.execution.postgres_snapshot_store import PostgresSnapshotStore
from app.infrastructure.execution.postgres_timer_store import PostgresTimerStore
from app.infrastructure.observability.execution_metrics import (
    record_optimistic_conflict,
    record_replay_failure,
)
from app.infrastructure.security.db_authorization import configure_session_authorization

type EventStoreFactory = Callable[[AsyncSession], EventStore]
type InboxFactory = Callable[[AsyncSession], PostgresInbox]
type SnapshotStoreFactory = Callable[[AsyncSession], PostgresSnapshotStore]


class SqlAlchemyExecutionOrchestrator:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        aggregates: Mapping[str, Aggregate],
        authorization: AuthorizationContext,
        event_store_factory: EventStoreFactory = PostgresEventStore,
        inbox_factory: InboxFactory = PostgresInbox,
        snapshot_store_factory: SnapshotStoreFactory = PostgresSnapshotStore,
        now: Callable[[], datetime] | None = None,
        claim_ttl: timedelta = timedelta(seconds=30),
        max_conflict_retries: int = 3,
        snapshot_interval: int = 50,
    ) -> None:
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        if max_conflict_retries <= 0:
            raise ValueError("max_conflict_retries must be positive")
        if snapshot_interval <= 0:
            raise ValueError("snapshot_interval must be positive")
        self._session_factory = session_factory
        self._aggregates = dict(aggregates)
        self._authorization = authorization
        self._event_store_factory = event_store_factory
        self._inbox_factory = inbox_factory
        self._snapshot_store_factory = snapshot_store_factory
        self._now = now or (lambda: datetime.now(UTC))
        self._claim_ttl = claim_ttl
        self._max_conflict_retries = max_conflict_retries
        self._snapshot_interval = snapshot_interval

    async def handle(self, command: CommandEnvelope) -> CommandResult:
        async with self._session_factory() as session:
            try:
                await configure_session_authorization(session, self._authorization)
                inbox = self._inbox_factory(session)
                claim = await inbox.claim(
                    command,
                    now=self._now(),
                    claim_ttl=self._claim_ttl,
                )
                if claim.status == "completed":
                    assert claim.result is not None
                    await session.commit()
                    return claim.result

                if claim.payload_too_large:
                    result = self._rejected(
                        command,
                        RejectionCode.PAYLOAD_TOO_LARGE,
                    )
                    await inbox.complete(result, now=self._now())
                    await session.commit()
                    return result

                result = await self._process(session, command)
                await inbox.complete(result, now=self._now())
                await session.commit()
                return result
            except CommandInProgressError:
                # Another worker holds an active claim. This is a benign
                # concurrency signal, not a fatal error: roll back without
                # touching the inbox row and report a non-terminal "deferred"
                # outcome so the command is retried later instead of crashing
                # the control plane.
                await session.rollback()
                return self._deferred(command)
            except (OSError, RuntimeError, ValueError):
                await session.rollback()
                raise

    async def _process(
        self,
        session: AsyncSession,
        command: CommandEnvelope,
    ) -> CommandResult:
        aggregate = self._aggregates.get(command.stream_type)
        if aggregate is None:
            return self._rejected(command, RejectionCode.UNKNOWN_COMMAND)
        try:
            aggregate.command_registry.latest(command.command_type)
        except KeyError:
            return self._rejected(command, RejectionCode.UNKNOWN_COMMAND)
        try:
            command_schema_version, command_payload = aggregate.command_registry.upcast(
                command.command_type,
                command.command_schema_version,
                command.payload,
            )
        except (KeyError, ValidationError):
            return self._rejected(command, RejectionCode.INVALID_COMMAND_SCHEMA)
        normalized_command = CommandEnvelope.model_validate(
            {
                **command.model_dump(mode="python"),
                "command_schema_version": command_schema_version,
                "payload": command_payload,
            }
        )

        store = self._event_store_factory(session)
        snapshot_store = self._snapshot_store_factory(session)
        stream = StreamRef(
            stream_type=command.stream_type,
            stream_id=command.stream_id,
        )
        for _ in range(self._max_conflict_retries):
            snapshot = await snapshot_store.load(
                stream.stream_type,
                stream.stream_id,
                state_type=aggregate.state_type,
                serializer_version=aggregate.snapshot_serializer_version,
            )
            if snapshot is None:
                stored_events = await store.load_stream(
                    stream.stream_type,
                    stream.stream_id,
                )
                replayed = replay(
                    aggregate,
                    self._upcast_events(aggregate, stored_events),
                    stream_id=stream.stream_id,
                )
            else:
                try:
                    stored_events = await store.load_stream(
                        stream.stream_type,
                        stream.stream_id,
                        after_version=snapshot.stream_version,
                        expected_previous_hash=snapshot.last_event_hash,
                    )
                except CorruptEventStreamError:
                    stored_events = await store.load_stream(
                        stream.stream_type,
                        stream.stream_id,
                    )
                    record_replay_failure("snapshot_hash_mismatch")
                    await snapshot_store.delete(
                        stream.stream_type,
                        stream.stream_id,
                    )
                    replayed = replay(
                        aggregate,
                        self._upcast_events(aggregate, stored_events),
                        stream_id=stream.stream_id,
                    )
                else:
                    replayed = replay(
                        aggregate,
                        self._upcast_events(aggregate, stored_events),
                        snapshot=snapshot,
                        stream_id=stream.stream_id,
                    )
            try:
                decision = aggregate.decide(replayed.state, normalized_command)
            except UnknownRunCommandError:
                return self._rejected(command, RejectionCode.UNKNOWN_COMMAND)
            except ExpectedStreamVersionError:
                return self._rejected(
                    command,
                    RejectionCode.EXPECTED_VERSION_CONFLICT,
                )
            except InvalidRunTransitionError:
                return self._rejected(command, RejectionCode.INVALID_TRANSITION)

            try:
                appended = await store.append(
                    stream,
                    replayed.stream_version,
                    decision.events,
                    AppendContext(
                        owner_user_id=command.owner_user_id,
                        team_id=command.team_id,
                        correlation_id=command.correlation_id,
                        causation_id=command.command_id,
                        occurred_at=self._now(),
                    ),
                )
            except OptimisticConcurrencyError:
                record_optimistic_conflict()
                continue
            except PayloadTooLargeError:
                return self._rejected(command, RejectionCode.PAYLOAD_TOO_LARGE)

            await self._write_critical_records(
                session,
                command=command,
                decision=decision,
                appended_events=appended.events,
            )
            await self._save_snapshot_if_due(
                snapshot_store,
                aggregate=aggregate,
                command=command,
                replayed=replayed,
                appended_events=appended.events,
            )
            return CommandResult(
                command_id=command.command_id,
                status="accepted",
                first_event_position=appended.first_position,
                last_event_position=appended.last_position,
                rejection_code=None,
            )

        return self._rejected(command, RejectionCode.CONCURRENCY_CONFLICT)

    async def _save_snapshot_if_due(
        self,
        snapshot_store: PostgresSnapshotStore,
        *,
        aggregate: Aggregate,
        command: CommandEnvelope,
        replayed: ReplayResult,
        appended_events: tuple,
    ) -> None:
        if not appended_events:
            return
        stream_version = appended_events[-1].stream_version
        is_terminal = any(
            event.event_type in {"RunCompleted", "RunFailed", "RunCancelled"}
            for event in appended_events
        )
        if not is_terminal and stream_version % self._snapshot_interval != 0:
            return
        updated = replay(
            aggregate,
            appended_events,
            snapshot=ReplaySnapshot(
                stream_id=command.stream_id,
                stream_version=replayed.stream_version,
                state=replayed.state,
                state_hash=replayed.state_hash,
                last_event_hash=replayed.last_event_hash,
            ),
            stream_id=command.stream_id,
        )
        await snapshot_store.save(
            command.stream_type,
            ReplaySnapshot(
                stream_id=command.stream_id,
                stream_version=updated.stream_version,
                state=updated.state,
                state_hash=updated.state_hash,
                last_event_hash=updated.last_event_hash,
            ),
            owner_user_id=command.owner_user_id,
            team_id=command.team_id,
            serializer_version=aggregate.snapshot_serializer_version,
        )

    @staticmethod
    def _upcast_events(
        aggregate: Aggregate,
        events: tuple,
    ) -> tuple:
        upcasted = []
        for event in events:
            schema_version, payload = aggregate.event_registry.upcast(
                event.event_type,
                event.event_schema_version,
                event.public_payload,
            )
            upcasted.append(
                event.__class__.model_validate(
                    {
                        **event.model_dump(mode="python"),
                        "event_schema_version": schema_version,
                        "public_payload": payload,
                    }
                )
            )
        return tuple(upcasted)

    @staticmethod
    async def _write_critical_records(
        session: AsyncSession,
        *,
        command: CommandEnvelope,
        decision: Decision,
        appended_events: tuple,
    ) -> None:
        await PostgresTimerStore(session).cancel_matching(appended_events)
        for event in appended_events:
            terminal_activity_status = {
                "ActivityCompleted": "succeeded",
                "ActivityFailed": "failed",
                "ActivityOutcomeUnknown": "unknown",
                "ActivityCancelled": "cancelled",
            }.get(event.event_type)
            if terminal_activity_status is None:
                continue
            activity_id = UUID(str(event.public_payload["activity_id"]))
            completed = await session.scalar(
                update(ExecutionActivityTaskORM)
                .where(
                    ExecutionActivityTaskORM.activity_id == activity_id,
                    ExecutionActivityTaskORM.request_generation
                    == int(event.public_payload["generation"]),
                    ExecutionActivityTaskORM.status.in_(("pending", "claimed", "call_started")),
                )
                .values(
                    status=terminal_activity_status,
                    result_ref=event.public_payload.get("result_ref"),
                    result_summary=event.public_payload.get("result_summary"),
                    failure_code=event.public_payload.get("failure_code"),
                    completed_at=event.occurred_at,
                    claimed_by=None,
                    claim_deadline=None,
                    heartbeat_at=None,
                    updated_at=event.occurred_at,
                )
                .returning(ExecutionActivityTaskORM.activity_id)
            )
            if completed is None:
                raise RuntimeError("formal Activity settlement has no active operational task")
        terminal = next(
            (
                event
                for event in appended_events
                if event.event_type in {"RunCancelled", "RunFailed", "RunCompleted"}
            ),
            None,
        )
        if terminal is not None and command.stream_type == "run":
            await session.execute(
                update(ExecutionActivityTaskORM)
                .where(
                    ExecutionActivityTaskORM.aggregate_type == "run",
                    ExecutionActivityTaskORM.aggregate_id == command.stream_id,
                    ExecutionActivityTaskORM.status.in_(("pending", "claimed", "call_started")),
                )
                .values(
                    status="cancelled",
                    failure_code=(f"RUN_{terminal.event_type.removeprefix('Run').upper()}"),
                    completed_at=terminal.occurred_at,
                    claimed_by=None,
                    claim_deadline=None,
                    heartbeat_at=None,
                    updated_at=terminal.occurred_at,
                )
            )
        for event in appended_events:
            session.add(
                ExecutionOutboxORM(
                    outbox_id=uuid4(),
                    event_position=event.position,
                    destination="execution.events",
                    dedupe_key=f"event:{event.event_id}",
                    owner_user_id=command.owner_user_id,
                    team_id=command.team_id,
                )
            )

        if (decision.activity_requests or decision.scheduled_commands) and not appended_events:
            raise RuntimeError("external work must be caused by a persisted event")
        request_position = appended_events[-1].position if appended_events else None
        for activity in decision.activity_requests:
            session.add(
                ExecutionActivityTaskORM(
                    activity_id=activity.activity_id,
                    run_id=(activity.aggregate_id if activity.aggregate_type == "run" else None),
                    aggregate_type=activity.aggregate_type,
                    aggregate_id=activity.aggregate_id,
                    activity_type=activity.activity_type,
                    request_event_position=request_position,
                    owner_user_id=command.owner_user_id,
                    team_id=command.team_id,
                    request_generation=activity.generation,
                    timeout_at=activity.timeout_at,
                    request_ref=activity.input_ref,
                    request_digest=activity.input_digest,
                    request_payload=activity.input_payload,
                )
            )
        for scheduled in decision.scheduled_commands:
            session.add(
                ExecutionScheduledCommandORM(
                    timer_id=scheduled.timer_id,
                    due_at=scheduled.due_at,
                    command_envelope=scheduled.command.model_dump(mode="json"),
                    cancellation_event_types=sorted(scheduled.cancellation_event_types),
                    cancellation_activity_id=scheduled.cancellation_activity_id,
                    owner_user_id=command.owner_user_id,
                    team_id=command.team_id,
                )
            )

    @staticmethod
    def _rejected(
        command: CommandEnvelope,
        code: RejectionCode,
    ) -> CommandResult:
        return CommandResult(
            command_id=command.command_id,
            status="rejected",
            first_event_position=None,
            last_event_position=None,
            rejection_code=code.value,
        )

    @staticmethod
    def _deferred(command: CommandEnvelope) -> CommandResult:
        return CommandResult(
            command_id=command.command_id,
            status="deferred",
            first_event_position=None,
            last_event_position=None,
            rejection_code=None,
        )


__all__ = ["SqlAlchemyExecutionOrchestrator"]
