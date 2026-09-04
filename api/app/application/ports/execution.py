"""Application contracts for durable formal execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.execution.commands import CommandEnvelope
from app.domain.models.scope import OwnerScope


class CommandResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    command_id: UUID
    # "deferred" is a non-terminal, non-fatal outcome: another worker holds an
    # active claim on this command, so it was neither accepted nor rejected and
    # remains eligible for a later retry. The inbox row is left untouched.
    status: Literal["accepted", "rejected", "deferred"]
    first_event_position: int | None
    last_event_position: int | None
    rejection_code: str | None


@dataclass(frozen=True)
class WakeupMessage:
    destination: str
    dedupe_key: str
    event_position: int


@dataclass(frozen=True)
class OutboxClaim:
    outbox_id: UUID
    event_position: int
    destination: str
    dedupe_key: str
    generation: int
    attempt: int
    # Destination-specific message body (K4-2); None for plain wakeup hints.
    payload: dict | None = None


@dataclass(frozen=True)
class ApprovalWaitingNotice:
    """One approval lifecycle fact that needs to reach reviewers.

    ``user_id`` targets a single recipient; a purely team-owned Run sets it to
    None and carries ``team_id`` instead, and the notifier fans out to the
    team's reviewers. ``kind`` is "approval_waiting" (pending, needs a
    decision) or "approval_expired" (TTL elapsed, Run was cancelled).
    """

    user_id: str | None
    approval_id: UUID
    run_id: UUID
    session_id: str | None
    subject_label: str
    team_id: str | None = None
    kind: str = "approval_waiting"


@runtime_checkable
class ApprovalNotifierPort(Protocol):
    """Collaborator that turns a durable approval fact into a reviewer ping."""

    async def approval_waiting(self, notice: ApprovalWaitingNotice) -> None: ...


@dataclass(frozen=True)
class FormalProjectorResult:
    processed: int
    last_position: int
    # True when the scope's projection lock was held elsewhere and this pass
    # skipped without doing (or attempting) any work (P2-18). Callers that must
    # make progress (rebuild) retry; the pending-scope loop just moves on.
    busy: bool = False


@dataclass(frozen=True)
class TimerFireResult:
    claimed: int
    fired: int
    failed: int


@runtime_checkable
class ExecutionCommandHandlerPort(Protocol):
    async def handle(self, command: CommandEnvelope) -> CommandResult: ...


@runtime_checkable
class CommandEnvelopeWriterPort(Protocol):
    async def receive(self, command: CommandEnvelope) -> bool: ...


@runtime_checkable
class WakeupPublisherPort(Protocol):
    async def publish(self, message: WakeupMessage) -> None: ...


@runtime_checkable
class OutboxStorePort(Protocol):
    async def claim_batch(
        self,
        *,
        limit: int,
        now: datetime,
        claim_ttl: timedelta,
    ) -> tuple[OutboxClaim, ...]: ...

    async def mark_delivered(self, claim: OutboxClaim, *, now: datetime) -> bool: ...

    async def mark_failed(
        self,
        claim: OutboxClaim,
        *,
        now: datetime,
        error_type: str,
        base_retry_delay: timedelta,
        max_retry_delay: timedelta,
    ) -> bool: ...


@runtime_checkable
class TimerDispatcherPort(Protocol):
    async def fire_due(
        self,
        *,
        limit: int,
        now: datetime,
        claim_ttl: timedelta,
    ) -> TimerFireResult: ...


@runtime_checkable
class FormalProjectorPort(Protocol):
    async def run_once(
        self,
        owner_scope: OwnerScope,
        *,
        limit: int,
        through_position: int | None = None,
    ) -> FormalProjectorResult: ...

    async def rebuild(
        self,
        owner_scope: OwnerScope,
        *,
        through_position: int | None = None,
        batch_size: int = 1000,
    ) -> FormalProjectorResult: ...


@runtime_checkable
class OwnerScopeSourcePort(Protocol):
    async def list_pending(self, *, limit: int) -> tuple[OwnerScope, ...]: ...

    async def quarantine(
        self,
        owner_scope: OwnerScope,
        *,
        reason: str,
        error: str,
        failure_count: int,
    ) -> None:
        """Durably exclude one owner scope from pending-projection discovery."""
        ...
