"""PostgreSQL idempotency inbox for execution Commands."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.execution.commands import CommandEnvelope, normalize_utc
from app.domain.execution.errors import CommandInProgressError
from app.domain.execution.serialization import canonical_json_bytes
from app.infrastructure.execution.models import ExecutionCommandInboxORM

if TYPE_CHECKING:
    from app.application.execution.orchestrator import CommandResult


@dataclass(frozen=True)
class InboxClaim:
    status: Literal["claimed", "completed"]
    generation: int
    result: CommandResult | None = None
    payload_too_large: bool = False


class PostgresInbox:
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

    async def receive(self, command: CommandEnvelope) -> bool:
        if command.payload_digest is not None:
            raise ValueError("an omitted-payload envelope cannot create an inbox row")
        payload_bytes = canonical_json_bytes(command.payload)
        payload_too_large = len(payload_bytes) > self._max_payload_bytes
        payload_digest = (
            f"sha256:{hashlib.sha256(payload_bytes).hexdigest()}" if payload_too_large else None
        )
        statement = (
            pg_insert(ExecutionCommandInboxORM)
            .values(
                command_id=command.command_id,
                command_type=command.command_type,
                command_schema_version=command.command_schema_version,
                stream_type=command.stream_type,
                stream_id=command.stream_id,
                expected_stream_version=command.expected_stream_version,
                owner_user_id=command.owner_user_id,
                team_id=command.team_id,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
                issued_at=command.issued_at,
                payload={} if payload_too_large else command.payload,
                payload_digest=payload_digest,
                status="received",
                last_error_code=("PAYLOAD_TOO_LARGE" if payload_too_large else None),
            )
            .on_conflict_do_nothing(index_elements=["command_id"])
            .returning(ExecutionCommandInboxORM.command_id)
        )
        inserted = await self._session.scalar(statement)
        if inserted is not None:
            return True
        record = await self._session.scalar(
            select(ExecutionCommandInboxORM).where(
                ExecutionCommandInboxORM.command_id == command.command_id
            )
        )
        if record is None:
            raise RuntimeError("command inbox conflict row is not visible")
        self._assert_same_command(record, command)
        return False

    async def claim(
        self,
        command: CommandEnvelope,
        *,
        now: datetime,
        claim_ttl: timedelta,
    ) -> InboxClaim:
        resolved_now = normalize_utc(now)
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        if command.payload_digest is None:
            await self.receive(command)
        # SKIP LOCKED: if a concurrent worker holds the row lock (is actively
        # claiming/processing this command), the lock is skipped and no row is
        # returned. That is the concurrency signal, surfaced as a non-fatal
        # CommandInProgressError rather than blocking on the lock. The row this
        # session just receive()d, or one a peer left in ``processing`` and then
        # released, is lockable here and continues normally.
        record = await self._session.scalar(
            select(ExecutionCommandInboxORM)
            .where(ExecutionCommandInboxORM.command_id == command.command_id)
            .with_for_update(skip_locked=True)
        )
        if record is None:
            raise CommandInProgressError(
                f"command {command.command_id} is locked by a concurrent claim"
            )
        self._assert_same_command(record, command)

        if record.status in {"accepted", "rejected"}:
            return InboxClaim(
                status="completed",
                generation=record.claim_generation,
                result=self._persisted_result(record),
            )

        record.status = "processing"
        record.claim_generation += 1
        record.processing_started_at = resolved_now
        record.claim_deadline = resolved_now + claim_ttl
        await self._session.flush()
        return InboxClaim(
            status="claimed",
            generation=record.claim_generation,
            payload_too_large=record.last_error_code == "PAYLOAD_TOO_LARGE",
        )

    async def complete(
        self,
        result: CommandResult,
        *,
        now: datetime,
    ) -> None:
        record = await self._session.scalar(
            select(ExecutionCommandInboxORM)
            .where(ExecutionCommandInboxORM.command_id == result.command_id)
            .with_for_update()
        )
        if record is None:
            raise RuntimeError("cannot complete a missing command inbox row")
        if record.status in {"accepted", "rejected"}:
            if self._persisted_result(record) != result:
                raise RuntimeError("command result conflicts with persisted result")
            return
        if record.status != "processing":
            raise RuntimeError(f"cannot complete inbox status {record.status}")
        record.status = result.status
        record.first_event_position = result.first_event_position
        record.last_event_position = result.last_event_position
        record.rejection_code = result.rejection_code
        record.processed_at = normalize_utc(now)
        record.claim_deadline = None
        await self._session.flush()

    @staticmethod
    def _assert_same_command(
        record: ExecutionCommandInboxORM,
        command: CommandEnvelope,
    ) -> None:
        persisted = (
            record.command_type,
            record.command_schema_version,
            record.stream_type,
            record.stream_id,
            record.expected_stream_version,
            record.owner_user_id,
            record.team_id,
            record.correlation_id,
            record.causation_id,
        )
        received = (
            command.command_type,
            command.command_schema_version,
            command.stream_type,
            command.stream_id,
            command.expected_stream_version,
            command.owner_user_id,
            command.team_id,
            command.correlation_id,
            command.causation_id,
        )
        if persisted != received:
            raise ValueError("command_id was reused with a different envelope")
        if record.payload_digest is None:
            if record.payload != command.payload:
                raise ValueError("command_id was reused with a different envelope")
            return
        received_digest = command.payload_digest or (
            f"sha256:{hashlib.sha256(canonical_json_bytes(command.payload)).hexdigest()}"
        )
        if record.payload_ref is not None or record.payload_digest != received_digest:
            raise ValueError("command_id was reused with a different envelope")

    @staticmethod
    def _persisted_result(record: ExecutionCommandInboxORM) -> CommandResult:
        from app.application.execution.orchestrator import CommandResult

        return CommandResult(
            command_id=record.command_id,
            status=record.status,
            first_event_position=record.first_event_position,
            last_event_position=record.last_event_position,
            rejection_code=record.rejection_code,
        )


__all__ = ["InboxClaim", "PostgresInbox"]
