"""Atomic PostgreSQL implementation of the kernel command transaction."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.authorization import AuthorizationContext
from app.kernel.application.ports import (
    CommandResult,
    CommandResultStatus,
    KernelAuthorization,
)
from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.decisions import Decision, DecisionFacts, StaleEffectClaim
from app.kernel.domain.events import ZERO_HASH, StoredEvent, event_hash, replay
from app.kernel.domain.types import EffectStatus, RunStatus

from .models import (
    KernelApprovalViewORM,
    KernelCommandORM,
    KernelEffectORM,
    KernelEffectViewORM,
    KernelEventORM,
    KernelMessageViewORM,
    KernelNotificationViewORM,
    KernelOutboxORM,
    KernelPublicEventORM,
    KernelResourceBuildViewORM,
    KernelRunORM,
    KernelRunViewORM,
    KernelTimerORM,
)
from .projections import ProjectionRegistry
from .session_auth import bind_context, command_context

EncryptPrivate = Callable[[dict[str, Any]], str]
DecryptPrivate = Callable[[str], dict[str, Any]]
CommandValidator = Callable[[AsyncSession, CommandEnvelope], Awaitable[None]]


def _scope_values(command: CommandEnvelope) -> dict[str, str | None]:
    return {
        "owner_user_id": command.owner_scope.owner_user_id,
        "team_id": command.owner_scope.team_id,
    }


def _request_digest(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _result_from_row(row: KernelCommandORM) -> CommandResult | None:
    if row.status not in {CommandResultStatus.SUCCEEDED.value, CommandResultStatus.REJECTED.value}:
        return None
    result = dict(row.result or {})
    return CommandResult(
        command_id=row.id,
        run_id=row.run_id,
        status=CommandResultStatus(row.status),
        stream_version=int(result.get("stream_version", 0)),
        event_ids=tuple(UUID(value) for value in result.get("event_ids", [])),
        error_code=row.error_code,
        error_message=row.error_message,
    )


class PostgresKernelTransaction:
    def __init__(
        self,
        session: AsyncSession,
        *,
        encrypt_private: EncryptPrivate,
        decrypt_private: DecryptPrivate,
        projections: ProjectionRegistry,
        command_validator: CommandValidator | None,
        authorization_context: AuthorizationContext,
    ) -> None:
        self._session = session
        self._encrypt_private = encrypt_private
        self._decrypt_private = decrypt_private
        self._projections = projections
        self._command_validator = command_validator
        self._authorization_context = authorization_context
        self._loaded_events: dict[UUID, tuple[StoredEvent, ...]] = {}

    async def get_command_result(self, command_id: UUID) -> CommandResult | None:
        row = await self._session.get(KernelCommandORM, command_id)
        return _result_from_row(row) if row is not None else None

    async def reserve_command(self, command: CommandEnvelope) -> CommandResult | None:
        statement = (
            insert(KernelCommandORM)
            .values(
                id=command.command_id,
                run_id=command.run_id,
                workflow=command.workflow.value,
                command_type=command.type,
                schema_version=command.schema_version,
                payload_ciphertext=self._encrypt_private(command.payload),
                payload_digest=_request_digest(command.payload),
                expected_stream_version=command.expected_stream_version,
                actor_user_id=command.actor_user_id,
                request_id=command.request_id,
                status="processing",
                result=None,
                error_code=None,
                error_message=None,
                submitted_at=command.submitted_at,
                completed_at=None,
                **_scope_values(command),
            )
            .on_conflict_do_nothing(index_elements=[KernelCommandORM.id])
            .returning(KernelCommandORM.id)
        )
        inserted = await self._session.scalar(statement)
        if inserted is not None:
            return None
        row = await self._session.scalar(
            select(KernelCommandORM)
            .where(KernelCommandORM.id == command.command_id)
            .with_for_update()
        )
        if row is None:
            raise RuntimeError("conflicting command disappeared")
        result = _result_from_row(row)
        if result is None:
            raise RuntimeError("conflicting command has no terminal result")
        return result

    async def load_events(self, run_id: UUID) -> tuple[StoredEvent, ...]:
        lock_key = run_id.int % (1 << 63)
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
        )
        await self._session.scalar(
            select(KernelRunORM).where(KernelRunORM.id == run_id).with_for_update()
        )
        rows = (
            await self._session.scalars(
                select(KernelEventORM)
                .where(KernelEventORM.run_id == run_id)
                .order_by(KernelEventORM.version)
            )
        ).all()
        events = tuple(self._stored_event(row) for row in rows)
        replay(events)
        self._loaded_events[run_id] = events
        return events

    async def validate_command(self, command: CommandEnvelope) -> None:
        if self._command_validator is not None:
            try:
                await self._command_validator(self._session, command)
            finally:
                await bind_context(self._session, self._authorization_context)
        if command.type not in {
            "EffectSucceeded",
            "EffectFailed",
            "EffectOutcomeUnknown",
        }:
            return
        try:
            effect_id = UUID(str(command.payload["effect_id"]))
            generation = int(command.payload["claim_generation"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StaleEffectClaim("Effect outcome has no valid claim identity") from exc
        row = await self._session.scalar(
            select(KernelEffectORM).where(KernelEffectORM.id == effect_id).with_for_update()
        )
        expected_status = (
            EffectStatus.UNKNOWN.value
            if command.type == "EffectOutcomeUnknown"
            else EffectStatus.STARTED.value
        )
        if (
            row is None
            or row.run_id != command.run_id
            or row.claim_generation != generation
            or row.status != expected_status
            or row.owner_user_id != command.owner_scope.owner_user_id
            or row.team_id != command.owner_scope.team_id
        ):
            raise StaleEffectClaim("Effect outcome does not own the current claim")

    async def append_decision(
        self,
        command: CommandEnvelope,
        decision: Decision,
        facts: DecisionFacts,
    ) -> CommandResult:
        previous = list(self._loaded_events.get(command.run_id, ()))
        previous_hash = previous[-1].hash if previous else ZERO_HASH
        next_version = len(previous) + 1
        run = await self._session.get(KernelRunORM, command.run_id)
        if run is None:
            if not decision.events or decision.events[0].type != "RunStarted":
                raise RuntimeError("new Run decision must begin with RunStarted")
            run = KernelRunORM(
                id=command.run_id,
                workflow=command.workflow.value,
                created_by_user_id=command.actor_user_id,
                stream_version=0,
                stream_hash=ZERO_HASH,
                snapshot_version=None,
                snapshot_hash=None,
                snapshot=None,
                created_at=facts.now,
                updated_at=facts.now,
                **_scope_values(command),
            )
            self._session.add(run)
            await self._session.flush()

        stored_events: list[StoredEvent] = []
        private_payloads: list[dict[str, Any]] = []
        for offset, new_event in enumerate(decision.events):
            private_ciphertext = self._encrypt_private(new_event.private_payload)
            unsigned = StoredEvent(
                event_id=new_event.event_id,
                run_id=command.run_id,
                version=next_version + offset,
                type=new_event.type,
                schema_version=new_event.schema_version,
                public_payload=new_event.public_payload,
                private_payload_ciphertext=private_ciphertext,
                previous_hash=previous_hash,
                hash="",
                owner_scope=command.owner_scope,
                actor_user_id=command.actor_user_id,
                request_id=command.request_id,
                causation_id=command.command_id,
                correlation_id=command.run_id,
                occurred_at=new_event.occurred_at,
            )
            stored = unsigned.model_copy(update={"hash": event_hash(previous_hash, unsigned)})
            self._session.add(
                KernelEventORM(
                    run_id=stored.run_id,
                    version=stored.version,
                    event_id=stored.event_id,
                    event_type=stored.type,
                    schema_version=stored.schema_version,
                    public_payload=stored.public_payload,
                    private_payload_ciphertext=stored.private_payload_ciphertext,
                    previous_hash=stored.previous_hash,
                    hash=stored.hash,
                    actor_user_id=stored.actor_user_id,
                    request_id=stored.request_id,
                    causation_id=stored.causation_id,
                    correlation_id=stored.correlation_id,
                    occurred_at=stored.occurred_at,
                    **_scope_values(command),
                )
            )
            stored_events.append(stored)
            private_payloads.append(new_event.private_payload)
            previous_hash = stored.hash

        requested_event_ids = {
            UUID(str(event.public_payload["effect_id"])): event.event_id
            for event in stored_events
            if event.type == "EffectRequested"
        }
        if set(requested_event_ids) != {effect.effect_id for effect in decision.effects}:
            raise RuntimeError("Effect declarations must match EffectRequested events exactly")
        for effect in decision.effects:
            self._session.add(
                KernelEffectORM(
                    id=effect.effect_id,
                    invocation_id=effect.invocation_id,
                    run_id=command.run_id,
                    source_event_id=requested_event_ids[effect.effect_id],
                    effect_type=effect.type,
                    safety=effect.safety.value,
                    request_ciphertext=self._encrypt_private(effect.request),
                    request_digest=_request_digest(effect.request),
                    public_summary=effect.public_summary,
                    status=(
                        EffectStatus.BLOCKED.value
                        if effect.requires_approval
                        else EffectStatus.READY.value
                    ),
                    approval_id=effect.approval_id,
                    timeout_seconds=effect.timeout_seconds,
                    max_attempts=effect.max_attempts,
                    attempt_count=0,
                    next_attempt_at=facts.now,
                    claim_owner=None,
                    claim_generation=0,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    started_at=None,
                    result_reference=None,
                    result_digest=None,
                    error_code=None,
                    error_message=None,
                    created_at=facts.now,
                    updated_at=facts.now,
                    **_scope_values(command),
                )
            )
        for timer in decision.timers:
            self._session.add(
                KernelTimerORM(
                    id=timer.timer_id,
                    run_id=command.run_id,
                    due_at=timer.due_at,
                    command_type=timer.command_type,
                    command_payload=timer.command_payload,
                    status="pending",
                    claim_owner=None,
                    claim_generation=0,
                    lease_expires_at=None,
                    created_at=facts.now,
                    fired_at=None,
                    **_scope_values(command),
                )
            )
        await self._session.flush()
        await self._apply_operational_events(stored_events)
        for stored, private in zip(stored_events, private_payloads, strict=True):
            await self._projections.apply(self._session, stored, private)

        all_events = (*previous, *stored_events)
        state = replay(all_events)
        if state is None:
            raise RuntimeError("a successful command must produce or retain Run state")
        if state.status is not RunStatus.PURGED:
            run.stream_version = state.stream_version
            run.stream_hash = state.stream_hash
            run.snapshot_version = state.stream_version
            run.snapshot_hash = state.stream_hash
            run.snapshot = state.model_dump(mode="json")
            run.updated_at = facts.now

        result = CommandResult(
            command_id=command.command_id,
            run_id=command.run_id,
            status=CommandResultStatus.SUCCEEDED,
            stream_version=state.stream_version,
            event_ids=tuple(event.event_id for event in stored_events),
        )
        command_row = await self._session.get(KernelCommandORM, command.command_id)
        if command_row is None:
            raise RuntimeError("reserved command disappeared")
        command_row.status = result.status.value
        command_row.result = {
            "stream_version": result.stream_version,
            "event_ids": [str(value) for value in result.event_ids],
        }
        command_row.completed_at = facts.now
        self._session.add(
            KernelOutboxORM(
                topic="kernel.command.completed",
                key=str(command.run_id),
                payload={
                    "command_id": str(command.command_id),
                    "stream_version": result.stream_version,
                },
                created_at=facts.now,
                delivered_at=None,
            )
        )
        if state.status is RunStatus.PURGED:
            # Switch to a signed, narrowly recognized system actor before the
            # delete cascades through the otherwise append-only journal. The
            # terminal purge command remains as the non-sensitive tombstone.
            await bind_context(
                self._session,
                AuthorizationContext.system("kernel-purge"),
            )
            await self._session.execute(
                delete(KernelCommandORM).where(
                    KernelCommandORM.run_id == command.run_id,
                    KernelCommandORM.id != command.command_id,
                )
            )
            for projection in (
                KernelMessageViewORM,
                KernelEffectViewORM,
                KernelApprovalViewORM,
                KernelPublicEventORM,
                KernelResourceBuildViewORM,
            ):
                await self._session.execute(
                    delete(projection).where(projection.run_id == command.run_id)
                )
            await self._session.execute(
                delete(KernelRunViewORM).where(KernelRunViewORM.id == command.run_id)
            )
            await self._session.execute(
                delete(KernelNotificationViewORM).where(
                    KernelNotificationViewORM.payload["run_id"].as_string() == str(command.run_id)
                )
            )
            await self._session.delete(run)
        return result

    async def _apply_operational_events(self, events: list[StoredEvent]) -> None:
        """Advance durable work rows only from accepted journal events."""

        effect_statuses = {
            "EffectReleased": EffectStatus.READY.value,
            "EffectSucceeded": EffectStatus.SUCCEEDED.value,
            "EffectFailed": EffectStatus.FAILED.value,
            "EffectCancelled": EffectStatus.CANCELLED.value,
            "EffectOutcomeUnknown": EffectStatus.UNKNOWN.value,
        }
        for event in events:
            status = effect_statuses.get(event.type)
            if status is not None:
                effect_id = UUID(str(event.public_payload["effect_id"]))
                values: dict[str, Any] = {
                    "status": status,
                    "updated_at": event.occurred_at,
                }
                if event.type == "EffectReleased":
                    values.update(
                        claim_owner=None,
                        lease_expires_at=None,
                        heartbeat_at=None,
                        started_at=None,
                        next_attempt_at=event.occurred_at,
                    )
                await self._session.execute(
                    update(KernelEffectORM).where(KernelEffectORM.id == effect_id).values(**values)
                )
            if event.type == "ApprovalDecided" and event.public_payload.get("timer_id"):
                await self._session.execute(
                    update(KernelTimerORM)
                    .where(
                        KernelTimerORM.id == UUID(str(event.public_payload["timer_id"])),
                        KernelTimerORM.status.in_(("pending", "claimed")),
                    )
                    .values(status="cancelled", lease_expires_at=None)
                )

    async def reject_command(
        self,
        command: CommandEnvelope,
        *,
        code: str,
        message: str,
    ) -> CommandResult:
        row = await self._session.get(KernelCommandORM, command.command_id)
        if row is None:
            raise RuntimeError("reserved command disappeared")
        row.status = CommandResultStatus.REJECTED.value
        row.error_code = code
        row.error_message = message
        row.completed_at = command.submitted_at
        stream_version = len(self._loaded_events.get(command.run_id, ()))
        row.result = {"stream_version": stream_version, "event_ids": []}
        return _result_from_row(row)  # type: ignore[return-value]

    @staticmethod
    def _stored_event(row: KernelEventORM) -> StoredEvent:
        from app.kernel.domain.types import OwnerScopeRef

        return StoredEvent(
            event_id=row.event_id,
            run_id=row.run_id,
            version=row.version,
            type=row.event_type,
            schema_version=row.schema_version,
            public_payload=row.public_payload,
            private_payload_ciphertext=row.private_payload_ciphertext,
            previous_hash=row.previous_hash,
            hash=row.hash,
            owner_scope=OwnerScopeRef(owner_user_id=row.owner_user_id, team_id=row.team_id),
            actor_user_id=row.actor_user_id,
            request_id=row.request_id,
            causation_id=row.causation_id,
            correlation_id=row.correlation_id,
            occurred_at=row.occurred_at,
        )


class PostgresKernelStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        encrypt_private: EncryptPrivate,
        decrypt_private: DecryptPrivate,
        projections: ProjectionRegistry | None = None,
        command_validator: CommandValidator | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._encrypt_private = encrypt_private
        self._decrypt_private = decrypt_private
        self._projections = projections or ProjectionRegistry()
        self._command_validator = command_validator

    @asynccontextmanager
    async def transaction(
        self,
        authorization: KernelAuthorization,
    ) -> AsyncIterator[PostgresKernelTransaction]:
        async with self._session_factory() as session, session.begin():
            authorization_context = command_context(
                authorization,
                authorization.allowed_scopes[0],
                request_id="kernel-command",
            )
            await bind_context(session, authorization_context)
            yield PostgresKernelTransaction(
                session,
                encrypt_private=self._encrypt_private,
                decrypt_private=self._decrypt_private,
                projections=self._projections,
                command_validator=self._command_validator,
                authorization_context=authorization_context,
            )
