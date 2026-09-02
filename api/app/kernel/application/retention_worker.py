"""Bounded retention coordinator that submits auditable purge commands."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.types import OwnerScopeRef, Workflow

from .effect_worker import CommandSink
from .ports import KernelAuthorization


class RetentionCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: UUID
    resource_type: str
    resource_id: str
    workflow: Workflow
    owner_scope: OwnerScopeRef
    disposition_hash: str = Field(min_length=64, max_length=64)
    claim_generation: int = Field(ge=1)


class RetentionStore(Protocol):
    async def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> tuple[RetentionCandidate, ...]: ...

    async def mark_completed(
        self,
        candidate_id: UUID,
        claim_generation: int,
        *,
        now: datetime,
    ) -> bool: ...


class RetentionWorker:
    def __init__(
        self,
        *,
        store: RetentionStore,
        command_sink: CommandSink,
        worker_id: str,
        batch_size: int = 20,
        lease_seconds: int = 120,
    ) -> None:
        self._store = store
        self._command_sink = command_sink
        self._worker_id = worker_id
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds

    async def run_once(self, *, now: datetime) -> int:
        candidates = await self._store.claim_due(
            worker_id=self._worker_id,
            now=now,
            limit=self._batch_size,
            lease_seconds=self._lease_seconds,
        )
        processed = 0
        for candidate in candidates:
            if candidate.resource_type != "run":
                raise ValueError(f"unsupported retention resource: {candidate.resource_type}")
            run_id = UUID(candidate.resource_id)
            command = CommandEnvelope(
                command_id=candidate.candidate_id,
                run_id=run_id,
                workflow=candidate.workflow,
                type="PurgeRun",
                payload={
                    "disposition_hash": candidate.disposition_hash,
                    "retention_claim_generation": candidate.claim_generation,
                },
                expected_stream_version=None,
                owner_scope=candidate.owner_scope,
                actor_user_id="kernel-retention-worker",
                request_id=f"retention:{candidate.candidate_id}",
                submitted_at=now,
            )
            await self._command_sink.submit(
                command,
                KernelAuthorization.system("kernel-retention-worker", candidate.owner_scope),
            )
            await self._store.mark_completed(
                candidate.candidate_id,
                candidate.claim_generation,
                now=now,
            )
            processed += 1
        return processed
