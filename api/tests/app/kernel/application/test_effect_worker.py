"""Effect worker tests for durable starts, fencing, and unknown outcomes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.kernel.application.effect_worker import (
    EffectClaim,
    EffectExecutionResult,
    EffectRegistry,
    EffectWorker,
    ExpiredEffect,
)
from app.kernel.application.ports import KernelAuthorization
from app.kernel.domain.types import EffectSafety, OwnerScopeRef, Workflow

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
EFFECT_ID = UUID(int=6100)
RUN_ID = UUID(int=6101)


def _claim(
    *,
    safety: EffectSafety = EffectSafety.READ_ONLY,
    generation: int = 2,
    attempt_count: int = 1,
    max_attempts: int = 1,
    timeout_seconds: int = 30,
):
    return EffectClaim(
        effect_id=EFFECT_ID,
        invocation_id=EFFECT_ID,
        run_id=RUN_ID,
        workflow=Workflow.AGENT,
        effect_type="model.call",
        safety=safety,
        request={"prompt": "hello"},
        owner_scope=OwnerScopeRef.personal("user-1"),
        claim_generation=generation,
        timeout_seconds=timeout_seconds,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
    )


class FakeClaimStore:
    def __init__(self, *, claims=(), expired=()) -> None:
        self.claims = tuple(claims)
        self.expired = tuple(expired)
        self.started: list[tuple[UUID, int]] = []
        self.retried: list[tuple[UUID, int, str]] = []

    async def recover_expired(self, *, now):
        return self.expired

    async def claim_ready(self, *, worker_id, now, limit, lease_seconds):
        return self.claims

    async def mark_started(self, effect_id, claim_generation, *, now):
        self.started.append((effect_id, claim_generation))
        return True

    async def mark_retry(self, effect_id, claim_generation, *, now, code):
        self.retried.append((effect_id, claim_generation, code))
        return True


class RecordingSink:
    def __init__(self) -> None:
        self.commands = []
        self.authorizations: list[KernelAuthorization] = []

    async def submit(self, command, authorization):
        self.commands.append(command)
        self.authorizations.append(authorization)
        return object()


class RecordingHandler:
    def __init__(self, result: EffectExecutionResult) -> None:
        self.result = result
        self.claims: list[EffectClaim] = []

    async def execute(self, claim: EffectClaim) -> EffectExecutionResult:
        self.claims.append(claim)
        return self.result


@pytest.mark.asyncio
async def test_expired_non_idempotent_started_effect_becomes_unknown_without_recall() -> None:
    """A crash window after an unsafe write starts must never repeat the write."""

    claim = _claim(safety=EffectSafety.NON_IDEMPOTENT_WRITE)
    store = FakeClaimStore(
        expired=(ExpiredEffect(claim=claim, resolution="unknown"),),
    )
    sink = RecordingSink()
    handler = RecordingHandler(EffectExecutionResult.succeeded({"ignored": True}))
    worker = EffectWorker(
        store=store,
        handlers=EffectRegistry({"model.call": handler}),
        command_sink=sink,
        worker_id="worker-1",
    )

    processed = await worker.run_once(now=NOW)

    assert processed == 1
    assert handler.claims == []
    assert [command.type for command in sink.commands] == ["EffectOutcomeUnknown"]
    assert sink.commands[0].payload["claim_generation"] == 2


@pytest.mark.asyncio
async def test_claim_is_marked_started_before_handler_and_reports_success_as_command() -> None:
    """A handler result must return through the reducer instead of mutating the Run."""

    claim = _claim()
    store = FakeClaimStore(claims=(claim,))
    sink = RecordingSink()
    handler = RecordingHandler(EffectExecutionResult.succeeded({"content": "done"}))
    worker = EffectWorker(
        store=store,
        handlers=EffectRegistry({"model.call": handler}),
        command_sink=sink,
        worker_id="worker-1",
    )

    processed = await worker.run_once(now=NOW)

    assert processed == 1
    assert store.started == [(EFFECT_ID, 2)]
    assert handler.claims == [claim]
    command = sink.commands[0]
    assert command.command_id == EffectWorker.outcome_command_id(EFFECT_ID, 2, "succeeded")
    assert command.type == "EffectSucceeded"
    assert command.payload == {
        "effect_id": str(EFFECT_ID),
        "effect_type": "model.call",
        "claim_generation": 2,
        "content": "done",
    }
    assert sink.authorizations[0].is_system is True


@pytest.mark.asyncio
async def test_handler_failure_is_sanitized_and_reported() -> None:
    """Provider exceptions must not leak arbitrary payloads into public errors."""

    class FailingHandler:
        async def execute(self, claim):
            raise RuntimeError("secret-token=abc")

    sink = RecordingSink()
    worker = EffectWorker(
        store=FakeClaimStore(claims=(_claim(),)),
        handlers=EffectRegistry({"model.call": FailingHandler()}),
        command_sink=sink,
        worker_id="worker-1",
    )

    await worker.run_once(now=NOW + timedelta(seconds=1))

    command = sink.commands[0]
    assert command.type == "EffectFailed"
    assert command.payload["code"] == "effect_handler_failed"
    assert "secret-token" not in command.payload.get("message", "")


@pytest.mark.asyncio
async def test_transient_handler_failure_is_retried_before_terminal_failure() -> None:
    class FailingHandler:
        async def execute(self, claim):
            raise RuntimeError("provider unavailable")

    store = FakeClaimStore(claims=(_claim(attempt_count=1, max_attempts=3),))
    sink = RecordingSink()
    worker = EffectWorker(
        store=store,
        handlers=EffectRegistry({"model.call": FailingHandler()}),
        command_sink=sink,
        worker_id="worker-1",
    )

    assert await worker.run_once(now=NOW) == 1
    assert store.retried == [(EFFECT_ID, 2, "effect_handler_failed")]
    assert sink.commands == []


@pytest.mark.asyncio
async def test_effect_timeout_is_bounded_and_reported_without_provider_details() -> None:
    class HangingHandler:
        async def execute(self, claim):
            await asyncio.sleep(60)
            raise AssertionError("unreachable")

    sink = RecordingSink()
    worker = EffectWorker(
        store=FakeClaimStore(claims=(_claim(timeout_seconds=1),)),
        handlers=EffectRegistry({"model.call": HangingHandler()}),
        command_sink=sink,
        worker_id="worker-1",
    )

    assert await worker.run_once(now=NOW) == 1
    assert sink.commands[0].type == "EffectFailed"
    assert sink.commands[0].payload["code"] == "effect_timeout"
