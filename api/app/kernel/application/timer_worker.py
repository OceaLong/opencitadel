"""Fenced delivery of persistent timer commands."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.types import OwnerScopeRef, Workflow

from .effect_worker import CommandSink
from .ports import KernelAuthorization


class TimerClaim(BaseModel):
    model_config = ConfigDict(frozen=True)

    timer_id: UUID
    run_id: UUID
    workflow: Workflow
    command_type: str
    command_payload: dict[str, Any]
    owner_scope: OwnerScopeRef
    claim_generation: int = Field(ge=1)


class TimerClaimStore(Protocol):
    async def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> tuple[TimerClaim, ...]: ...

    async def mark_fired(
        self,
        timer_id: UUID,
        claim_generation: int,
        *,
        now: datetime,
    ) -> bool: ...


class TimerWorker:
    def __init__(
        self,
        *,
        store: TimerClaimStore,
        command_sink: CommandSink,
        worker_id: str,
        batch_size: int = 100,
        lease_seconds: int = 60,
    ) -> None:
        self._store = store
        self._command_sink = command_sink
        self._worker_id = worker_id
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds

    async def run_once(self, *, now: datetime) -> int:
        claims = await self._store.claim_due(
            worker_id=self._worker_id,
            now=now,
            limit=self._batch_size,
            lease_seconds=self._lease_seconds,
        )
        processed = 0
        for claim in claims:
            command = CommandEnvelope(
                command_id=claim.timer_id,
                run_id=claim.run_id,
                workflow=claim.workflow,
                type=claim.command_type,
                payload={
                    **claim.command_payload,
                    "timer_claim_generation": claim.claim_generation,
                },
                expected_stream_version=None,
                owner_scope=claim.owner_scope,
                actor_user_id="kernel-timer-worker",
                request_id=f"timer:{claim.timer_id}:{claim.claim_generation}",
                submitted_at=now,
            )
            await self._command_sink.submit(
                command,
                KernelAuthorization.system("kernel-timer-worker", claim.owner_scope),
            )
            await self._store.mark_fired(
                claim.timer_id,
                claim.claim_generation,
                now=now,
            )
            processed += 1
        return processed
