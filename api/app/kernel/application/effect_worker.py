"""Fenced execution of durable non-deterministic Effects."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.types import EffectSafety, OwnerScopeRef, Workflow

from .ports import KernelAuthorization


class EffectClaim(BaseModel):
    model_config = ConfigDict(frozen=True)

    effect_id: UUID
    invocation_id: UUID
    run_id: UUID
    workflow: Workflow
    effect_type: str
    safety: EffectSafety
    request: dict[str, Any]
    owner_scope: OwnerScopeRef
    claim_generation: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)
    attempt_count: int = Field(default=1, ge=1)
    max_attempts: int = Field(default=1, ge=1)


class ExpiredEffect(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    claim: EffectClaim
    resolution: str


class EffectExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EffectExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: EffectExecutionStatus
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def succeeded(cls, payload: dict[str, Any] | None = None):
        return cls(status=EffectExecutionStatus.SUCCEEDED, payload=payload or {})

    @classmethod
    def failed(cls, *, code: str, message: str | None = None):
        payload: dict[str, Any] = {"code": code}
        if message:
            payload["message"] = message
        return cls(status=EffectExecutionStatus.FAILED, payload=payload)


class EffectHandler(Protocol):
    async def execute(self, claim: EffectClaim) -> EffectExecutionResult: ...


class EffectClaimStore(Protocol):
    async def recover_expired(self, *, now: datetime) -> tuple[ExpiredEffect, ...]: ...

    async def claim_ready(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> tuple[EffectClaim, ...]: ...

    async def mark_started(
        self,
        effect_id: UUID,
        claim_generation: int,
        *,
        now: datetime,
    ) -> bool: ...

    async def mark_retry(
        self,
        effect_id: UUID,
        claim_generation: int,
        *,
        now: datetime,
        code: str,
    ) -> bool: ...


class CommandSink(Protocol):
    async def submit(self, command: CommandEnvelope, authorization: KernelAuthorization): ...


class EffectRegistry:
    def __init__(self, handlers: Mapping[str, EffectHandler]) -> None:
        self._handlers = dict(handlers)

    def require(self, effect_type: str) -> EffectHandler:
        try:
            return self._handlers[effect_type]
        except KeyError as exc:
            raise LookupError(f"no Effect handler registered for {effect_type}") from exc

    @property
    def effect_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))


class EffectWorker:
    def __init__(
        self,
        *,
        store: EffectClaimStore,
        handlers: EffectRegistry,
        command_sink: CommandSink,
        worker_id: str,
        batch_size: int = 50,
        lease_seconds: int = 60,
    ) -> None:
        if batch_size < 1 or lease_seconds < 1:
            raise ValueError("Effect worker bounds must be positive")
        self._store = store
        self._handlers = handlers
        self._command_sink = command_sink
        self._worker_id = worker_id
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds

    @staticmethod
    def outcome_command_id(effect_id: UUID, generation: int, outcome: str) -> UUID:
        return uuid5(NAMESPACE_URL, f"kernel-effect:{effect_id}:{generation}:{outcome}")

    async def run_once(self, *, now: datetime) -> int:
        processed = 0
        for expired in await self._store.recover_expired(now=now):
            if expired.resolution != "unknown":
                continue
            await self._submit_outcome(
                expired.claim,
                now=now,
                outcome="unknown",
                payload={"code": "effect_outcome_unknown"},
            )
            processed += 1

        claims = await self._store.claim_ready(
            worker_id=self._worker_id,
            now=now,
            limit=self._batch_size,
            lease_seconds=self._lease_seconds,
        )
        for claim in claims:
            if not await self._store.mark_started(
                claim.effect_id,
                claim.claim_generation,
                now=now,
            ):
                continue
            try:
                async with asyncio.timeout(claim.timeout_seconds):
                    result = await self._handlers.require(claim.effect_type).execute(claim)
            except TimeoutError:
                if claim.attempt_count < claim.max_attempts:
                    await self._store.mark_retry(
                        claim.effect_id,
                        claim.claim_generation,
                        now=now,
                        code="effect_timeout",
                    )
                    processed += 1
                    continue
                result = EffectExecutionResult.failed(code="effect_timeout")
            except Exception:  # noqa: BLE001 - Effect boundary sanitizes all provider failures
                if claim.attempt_count < claim.max_attempts:
                    await self._store.mark_retry(
                        claim.effect_id,
                        claim.claim_generation,
                        now=now,
                        code="effect_handler_failed",
                    )
                    processed += 1
                    continue
                result = EffectExecutionResult.failed(
                    code="effect_handler_failed",
                    message="Effect handler failed",
                )
            await self._submit_outcome(
                claim,
                now=now,
                outcome=result.status.value,
                payload=result.payload,
            )
            processed += 1
        return processed

    async def _submit_outcome(
        self,
        claim: EffectClaim,
        *,
        now: datetime,
        outcome: str,
        payload: dict[str, Any],
    ) -> None:
        command_type = {
            "succeeded": "EffectSucceeded",
            "failed": "EffectFailed",
            "unknown": "EffectOutcomeUnknown",
        }[outcome]
        command = CommandEnvelope(
            command_id=self.outcome_command_id(
                claim.effect_id,
                claim.claim_generation,
                outcome,
            ),
            run_id=claim.run_id,
            workflow=claim.workflow,
            type=command_type,
            payload={
                "effect_id": str(claim.effect_id),
                "effect_type": claim.effect_type,
                "claim_generation": claim.claim_generation,
                **payload,
            },
            expected_stream_version=None,
            owner_scope=claim.owner_scope,
            actor_user_id="kernel-effect-worker",
            request_id=f"effect:{claim.effect_id}:{claim.claim_generation}",
            submitted_at=now,
        )
        await self._command_sink.submit(
            command,
            KernelAuthorization.system("kernel-effect-worker", claim.owner_scope),
        )
