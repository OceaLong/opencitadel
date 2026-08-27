"""PostgreSQL implementation of the append-only execution Event Store."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.execution.events import NewEvent, StoredEvent
from app.domain.execution.serialization import canonical_json_bytes
from app.domain.execution.store import (
    ZERO_HASH,
    AppendContext,
    AppendResult,
    CorruptEventStreamError,
    OptimisticConcurrencyError,
    PayloadTooLargeError,
    StreamOwnerScopeMismatchError,
    StreamRef,
    calculate_event_hash,
    verify_event_hashes,
    verify_stream,
)
from app.infrastructure.execution.models import (
    ExecutionEventORM,
    ExecutionStreamOwnerORM,
)
from app.infrastructure.observability.execution_metrics import record_replay_failure


class PostgresEventStore:
    def __init__(
        self,
        session: AsyncSession,
        *,
        max_payload_bytes: int = 64 * 1024,
    ) -> None:
        if max_payload_bytes <= 0:
            raise ValueError("max_payload_bytes must be positive")
        self._session = session
        self._max_payload_bytes = max_payload_bytes

    async def load_stream(
        self,
        stream_type: str,
        stream_id: str,
        *,
        after_version: int = 0,
        expected_previous_hash: str | None = None,
    ) -> tuple[StoredEvent, ...]:
        if after_version < 0:
            raise ValueError("after_version must not be negative")
        if expected_previous_hash is not None and after_version == 0:
            raise ValueError("expected_previous_hash requires a positive after_version")
        if expected_previous_hash is not None:
            return await self._load_verified_tail(
                stream_type,
                stream_id,
                after_version=after_version,
                expected_previous_hash=expected_previous_hash,
            )
        rows = (
            await self._session.scalars(
                select(ExecutionEventORM)
                .where(
                    ExecutionEventORM.stream_type == stream_type,
                    ExecutionEventORM.stream_id == stream_id,
                )
                .order_by(ExecutionEventORM.stream_version.asc())
            )
        ).all()
        events = tuple(self._to_stored(row) for row in rows)
        try:
            verify_stream(events)
        except CorruptEventStreamError:
            record_replay_failure("event_hash_mismatch")
            raise
        return tuple(event for event in events if event.stream_version > after_version)

    async def _load_verified_tail(
        self,
        stream_type: str,
        stream_id: str,
        *,
        after_version: int,
        expected_previous_hash: str,
    ) -> tuple[StoredEvent, ...]:
        rows = (
            await self._session.scalars(
                select(ExecutionEventORM)
                .where(
                    ExecutionEventORM.stream_type == stream_type,
                    ExecutionEventORM.stream_id == stream_id,
                    ExecutionEventORM.stream_version >= after_version,
                )
                .order_by(ExecutionEventORM.stream_version.asc())
            )
        ).all()
        if not rows or rows[0].stream_version != after_version:
            raise CorruptEventStreamError(
                stream_version=after_version,
                reason="snapshot anchor event is missing",
            )
        anchor = self._to_stored(rows[0])
        if anchor.event_hash != expected_previous_hash:
            raise CorruptEventStreamError(
                stream_version=after_version,
                reason="snapshot anchor hash mismatch",
            )
        if calculate_event_hash(anchor) != anchor.event_hash:
            raise CorruptEventStreamError(
                stream_version=after_version,
                reason="snapshot anchor event hash mismatch",
            )
        tail = tuple(self._to_stored(row) for row in rows[1:])
        verify_stream(
            tail,
            previous_hash=expected_previous_hash,
            previous_version=after_version,
            stream_identity=(stream_type, stream_id),
            stream_owner_scope=(anchor.owner_user_id, anchor.team_id),
        )
        return tail

    async def read_all(
        self,
        *,
        after_position: int,
        limit: int,
    ) -> tuple[StoredEvent, ...]:
        if after_position < 0:
            raise ValueError("after_position must not be negative")
        if limit <= 0:
            raise ValueError("limit must be positive")
        rows = (
            await self._session.scalars(
                select(ExecutionEventORM)
                .where(ExecutionEventORM.position > after_position)
                .order_by(ExecutionEventORM.position.asc())
                .limit(limit)
            )
        ).all()
        events = tuple(self._to_stored(row) for row in rows)
        self._verify_position_read(events)
        return events

    async def read_scope(
        self,
        *,
        after_position: int,
        limit: int,
        owner_user_id: str | None,
        team_id: str | None,
        through_position: int | None = None,
    ) -> tuple[StoredEvent, ...]:
        if after_position < 0:
            raise ValueError("after_position must not be negative")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if through_position is not None and through_position < 0:
            raise ValueError("through_position must not be negative")
        scope_filter = self._scope_filter(
            owner_user_id=owner_user_id,
            team_id=team_id,
        )
        statement = select(ExecutionEventORM).where(
            ExecutionEventORM.position > after_position,
            scope_filter,
        )
        if through_position is not None:
            statement = statement.where(ExecutionEventORM.position <= through_position)
        rows = (
            await self._session.scalars(
                statement.order_by(ExecutionEventORM.position.asc()).limit(limit)
            )
        ).all()
        events = tuple(self._to_stored(row) for row in rows)
        self._verify_position_read(events)
        return events

    async def latest_scope_position(
        self,
        *,
        owner_user_id: str | None,
        team_id: str | None,
    ) -> int:
        latest = await self._session.scalar(
            select(func.max(ExecutionEventORM.position)).where(
                self._scope_filter(
                    owner_user_id=owner_user_id,
                    team_id=team_id,
                )
            )
        )
        return latest or 0

    async def append(
        self,
        stream: StreamRef,
        expected_version: int,
        events: Sequence[NewEvent],
        context: AppendContext,
    ) -> AppendResult:
        if expected_version < 0:
            raise ValueError("expected_version must not be negative")
        for event in events:
            self._validate_payload_size(event)

        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": self._advisory_lock_key(stream)},
        )
        await self._session.execute(
            pg_insert(ExecutionStreamOwnerORM)
            .values(
                stream_type=stream.stream_type,
                stream_id=stream.stream_id,
                owner_user_id=context.owner_user_id,
                team_id=context.team_id,
            )
            .on_conflict_do_nothing(index_elements=["stream_type", "stream_id"])
        )
        owner = await self._session.get(
            ExecutionStreamOwnerORM,
            (stream.stream_type, stream.stream_id),
        )
        if owner is None or (owner.owner_user_id, owner.team_id) != (
            context.owner_user_id,
            context.team_id,
        ):
            raise StreamOwnerScopeMismatchError(stream=stream)
        head = await self._session.scalar(
            select(ExecutionEventORM)
            .where(
                ExecutionEventORM.stream_type == stream.stream_type,
                ExecutionEventORM.stream_id == stream.stream_id,
            )
            .order_by(ExecutionEventORM.stream_version.desc())
            .limit(1)
        )
        actual_version = head.stream_version if head is not None else 0
        if head is not None and (
            head.owner_user_id,
            head.team_id,
        ) != (
            context.owner_user_id,
            context.team_id,
        ):
            raise StreamOwnerScopeMismatchError(stream=stream)
        if actual_version != expected_version:
            raise OptimisticConcurrencyError(
                expected_version=expected_version,
                actual_version=actual_version,
            )

        previous_hash = head.event_hash if head is not None else ZERO_HASH
        records: list[ExecutionEventORM] = []
        for ordinal, event in enumerate(events, start=1):
            provisional = StoredEvent(
                position=1,
                event_id=uuid4(),
                stream_type=stream.stream_type,
                stream_id=stream.stream_id,
                stream_version=expected_version + ordinal,
                event_type=event.event_type,
                event_schema_version=event.event_schema_version,
                public_payload=event.public_payload,
                internal_payload=event.internal_payload,
                secret_ref=event.secret_ref,
                owner_user_id=context.owner_user_id,
                team_id=context.team_id,
                correlation_id=context.correlation_id,
                causation_id=context.causation_id,
                occurred_at=context.occurred_at,
                prev_hash=previous_hash,
                event_hash=ZERO_HASH,
            )
            event_hash = calculate_event_hash(provisional)
            records.append(
                ExecutionEventORM(
                    event_id=provisional.event_id,
                    stream_type=provisional.stream_type,
                    stream_id=provisional.stream_id,
                    stream_version=provisional.stream_version,
                    event_type=provisional.event_type,
                    event_schema_version=provisional.event_schema_version,
                    public_payload=provisional.public_payload,
                    internal_payload=provisional.internal_payload,
                    secret_ref=provisional.secret_ref,
                    owner_user_id=provisional.owner_user_id,
                    team_id=provisional.team_id,
                    correlation_id=provisional.correlation_id,
                    causation_id=provisional.causation_id,
                    occurred_at=provisional.occurred_at,
                    prev_hash=provisional.prev_hash,
                    event_hash=event_hash,
                )
            )
            previous_hash = event_hash

        self._session.add_all(records)
        await self._session.flush()
        stored = tuple(self._to_stored(record) for record in records)
        return AppendResult(
            events=stored,
            first_position=stored[0].position if stored else None,
            last_position=stored[-1].position if stored else None,
        )

    def _validate_payload_size(self, event: NewEvent) -> None:
        payload = canonical_json_bytes(
            {
                "public_payload": event.public_payload,
                "internal_payload": event.internal_payload,
                "secret_ref": event.secret_ref,
            }
        )
        if len(payload) > self._max_payload_bytes:
            raise PayloadTooLargeError(
                size=len(payload),
                limit=self._max_payload_bytes,
            )

    @staticmethod
    def _verify_position_read(events: tuple[StoredEvent, ...]) -> None:
        try:
            verify_event_hashes(events)
        except CorruptEventStreamError:
            record_replay_failure("event_hash_mismatch")
            raise

    @staticmethod
    def _advisory_lock_key(stream: StreamRef) -> int:
        digest = hashlib.sha256(f"{stream.stream_type}\0{stream.stream_id}".encode()).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=True)

    @staticmethod
    def _scope_filter(
        *,
        owner_user_id: str | None,
        team_id: str | None,
    ):
        if (owner_user_id is None) == (team_id is None):
            raise ValueError("exactly one owner scope is required")
        if owner_user_id is not None:
            return ExecutionEventORM.owner_user_id == owner_user_id
        return ExecutionEventORM.team_id == team_id

    @staticmethod
    def _to_stored(record: ExecutionEventORM) -> StoredEvent:
        return StoredEvent(
            position=record.position,
            event_id=record.event_id,
            stream_type=record.stream_type,
            stream_id=record.stream_id,
            stream_version=record.stream_version,
            event_type=record.event_type,
            event_schema_version=record.event_schema_version,
            public_payload=record.public_payload,
            internal_payload=record.internal_payload,
            secret_ref=record.secret_ref,
            owner_user_id=record.owner_user_id,
            team_id=record.team_id,
            correlation_id=record.correlation_id,
            causation_id=record.causation_id,
            occurred_at=record.occurred_at,
            prev_hash=record.prev_hash,
            event_hash=record.event_hash,
        )


__all__ = ["PostgresEventStore"]
