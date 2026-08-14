#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from prometheus_client import REGISTRY

from app.domain.models.codebase import SessionMode
from app.domain.models.tool_approval import ApprovalStatus, hash_normalized_args
from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)
from app.domain.services.agents.tool_batch_executor import ToolBatchExecutor
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.capability_policy import (
    CapabilityDeniedError,
    CapabilityPolicy,
)


READ_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.CODE_READ,
    effect=ToolEffect.READ_ONLY,
    idempotency=ToolIdempotency.SAFE,
    approval=ApprovalMode.NEVER,
)
WRITE_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.WORKSPACE_WRITE,
    idempotency=ToolIdempotency.NON_IDEMPOTENT,
    approval=ApprovalMode.NEVER,
    concurrency_group="filesystem",
)
GATED_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.INTERACTIVE,
    idempotency=ToolIdempotency.NON_IDEMPOTENT,
    approval=ApprovalMode.ALWAYS,
    concurrency_group="browser",
)
GATED_READ_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.CODE_READ,
    effect=ToolEffect.READ_ONLY,
    idempotency=ToolIdempotency.SAFE,
    approval=ApprovalMode.ALWAYS,
)


class _Recorder:
    def __init__(self) -> None:
        self.invocations: list[str] = []
        self.active_reads = 0
        self.max_parallel_reads = 0
        self.active_by_group: dict[str, int] = {}
        self.max_parallel_by_group: dict[str, int] = {}

    async def invoke(self, name: str, *, group: str, delay: float = 0) -> str:
        self.invocations.append(name)
        if group == "read":
            self.active_reads += 1
            self.max_parallel_reads = max(
                self.max_parallel_reads,
                self.active_reads,
            )
        else:
            active = self.active_by_group.get(group, 0) + 1
            self.active_by_group[group] = active
            self.max_parallel_by_group[group] = max(
                self.max_parallel_by_group.get(group, 0),
                active,
            )
        try:
            await asyncio.sleep(delay)
            return name
        finally:
            if group == "read":
                self.active_reads -= 1
            else:
                self.active_by_group[group] -= 1


class _RecordingTool(BaseTool):
    name = "recorder"

    def __init__(self, recorder: _Recorder) -> None:
        super().__init__()
        self.recorder = recorder

    @tool(
        name="read_a",
        description="read a",
        parameters={"delay": {"type": "number"}},
        required=[],
        policy=READ_POLICY,
    )
    async def read_a(self, delay: float = 0) -> str:
        return await self.recorder.invoke("read_a", group="read", delay=delay)

    @tool(
        name="read_b",
        description="read b",
        parameters={"delay": {"type": "number"}},
        required=[],
        policy=READ_POLICY,
    )
    async def read_b(self, delay: float = 0) -> str:
        return await self.recorder.invoke("read_b", group="read", delay=delay)

    @tool(
        name="write_file",
        description="write file",
        parameters={
            "path": {"type": "string"},
            "delay": {"type": "number"},
        },
        required=["path"],
        policy=WRITE_POLICY,
    )
    async def write_file(self, path: str, delay: float = 0) -> str:
        return await self.recorder.invoke(
            f"write_file:{path}",
            group="filesystem",
            delay=delay,
        )

    @tool(
        name="browser_click",
        description="click",
        parameters={"target": {"type": "string"}},
        required=["target"],
        policy=GATED_POLICY,
    )
    async def browser_click(self, target: str, delay: float = 0) -> str:
        return await self.recorder.invoke(
            f"browser_click:{target}",
            group="browser",
            delay=delay,
        )

    @tool(
        name="read_secret",
        description="read secret",
        parameters={"key": {"type": "string"}},
        required=["key"],
        policy=GATED_READ_POLICY,
    )
    async def read_secret(self, key: str) -> str:
        return await self.recorder.invoke(
            f"read_secret:{key}",
            group="read",
        )


class _ApprovalRepository:
    def __init__(self) -> None:
        self.batches = {}

    async def save_approval_batch(self, batch):
        self.batches[batch.id] = batch

    async def get_pending_approval_batch(self, session_id):
        batches = [
            batch
            for batch in self.batches.values()
            if batch.session_id == session_id
            and batch.status == ApprovalStatus.PENDING
        ]
        return batches[-1] if batches else None

    async def get_approval_batch(self, batch_id):
        return self.batches.get(batch_id)

    async def decide_approval_call(self, tool_call_id, status, decided_by):
        for batch_id, batch in self.batches.items():
            for index, call in enumerate(batch.calls):
                if call.tool_call_id != tool_call_id:
                    continue
                calls = list(batch.calls)
                calls[index] = call.model_copy(
                    update={
                        "status": status,
                        "decided_by": decided_by,
                        "decided_at": datetime.now(timezone.utc),
                    }
                )
                batch_status = ApprovalStatus.PENDING
                if all(item.status != ApprovalStatus.PENDING for item in calls):
                    batch_status = (
                        ApprovalStatus.REJECTED
                        if any(
                            item.status == ApprovalStatus.REJECTED
                            for item in calls
                        )
                        else ApprovalStatus.APPROVED
                    )
                self.batches[batch_id] = batch.model_copy(
                    update={"calls": calls, "status": batch_status}
                )
                return calls[index]
        return None

    async def consume_approval_batch(self, batch_id):
        batch = self.batches.get(batch_id)
        if batch is None:
            return None
        if batch.expires_at <= datetime.now(timezone.utc):
            expired = batch.model_copy(update={"status": ApprovalStatus.EXPIRED})
            self.batches[batch_id] = expired
            return expired
        if batch.status == ApprovalStatus.APPROVED:
            stored = batch.model_copy(update={"status": ApprovalStatus.CONSUMED})
            self.batches[batch_id] = stored
            return stored.model_copy(update={"execution_claimed": True})
        return batch


def _counter_value(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


@pytest.fixture
def recorder():
    return _Recorder()


@pytest.fixture
def repository():
    return _ApprovalRepository()


@pytest.fixture
def executor(recorder, repository):
    return ToolBatchExecutor(
        session_id="s1",
        tools=[_RecordingTool(recorder)],
        approval_repository=repository,
        capability_policy=CapabilityPolicy.for_mode(SessionMode.AGENT),
    )


@pytest.mark.asyncio
async def test_gated_batch_executes_no_side_effect_before_approval(
    executor,
    repository,
    recorder,
):
    batch = await executor.preflight(
        [
            _call("tc1", "write_file", {"path": "one"}),
            _call("tc2", "browser_click", {"target": "submit"}),
        ]
    )

    result = await executor.execute(batch)

    assert result.waiting is True
    assert recorder.invocations == []
    assert result.approval_batch is repository.batches[batch.batch_id]
    assert [
        call.tool_call_id for call in result.approval_batch.calls
    ] == ["tc1", "tc2"]
    assert [
        call.status for call in result.approval_batch.calls
    ] == [ApprovalStatus.APPROVED, ApprovalStatus.PENDING]


@pytest.mark.asyncio
async def test_effectful_never_batch_is_persisted_and_claimed_before_invocation(
    recorder,
    repository,
):
    invocation_states = []

    async def invoke(tool_pack, function_name, arguments):
        invocation_states.append(
            repository.batches[next(iter(repository.batches))].status
        )
        return await tool_pack.invoke(function_name, **arguments)

    executor = ToolBatchExecutor(
        session_id="s1",
        tools=[_RecordingTool(recorder)],
        approval_repository=repository,
        capability_policy=CapabilityPolicy.for_mode(SessionMode.AGENT),
        invoke_call=invoke,
    )
    batch = await executor.preflight(
        [_call("tc1", "write_file", {"path": "one"})]
    )

    persisted = repository.batches[batch.batch_id]
    assert persisted.status == ApprovalStatus.APPROVED
    assert persisted.calls[0].status == ApprovalStatus.APPROVED
    assert recorder.invocations == []

    result = await executor.execute(batch)

    assert result.rejected_reason is None
    assert invocation_states == [ApprovalStatus.CONSUMED]
    assert recorder.invocations == ["write_file:one"]


@pytest.mark.asyncio
async def test_always_read_only_call_waits_without_invocation(
    executor,
    repository,
    recorder,
):
    batch = await executor.preflight(
        [_call("tc1", "read_secret", {"key": "token"})]
    )

    result = await executor.execute(batch)

    assert result.waiting is True
    assert result.approval_batch is repository.batches[batch.batch_id]
    assert result.approval_batch.calls[0].status == ApprovalStatus.PENDING
    assert recorder.invocations == []


@pytest.mark.asyncio
async def test_read_only_calls_parallelize_but_filesystem_calls_serialize(
    executor,
    recorder,
):
    batch = await executor.preflight(
        [
            _call("r1", "read_a", {"delay": 0.02}),
            _call("r2", "read_b", {"delay": 0.02}),
            _call("w1", "write_file", {"path": "one", "delay": 0.01}),
            _call("w2", "write_file", {"path": "two", "delay": 0.01}),
        ]
    )

    result = await executor.execute(batch)

    assert result.waiting is False
    assert [item.tool_call_id for item in result.calls] == [
        "r1",
        "r2",
        "w1",
        "w2",
    ]
    assert recorder.max_parallel_reads == 2
    assert recorder.max_parallel_by_group["filesystem"] == 1


@pytest.mark.asyncio
async def test_expired_approval_batch_executes_nothing(
    executor,
    repository,
    recorder,
):
    batch = await executor.preflight(
        [_call("tc1", "browser_click", {"target": "submit"})]
    )
    repository.batches[batch.batch_id] = repository.batches[
        batch.batch_id
    ].model_copy(
        update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
    )

    result = await executor.resume(batch.batch_id, actor_id="u1")

    assert result.rejected_reason == "approval_expired"
    assert recorder.invocations == []


@pytest.mark.asyncio
async def test_partial_approval_executes_nothing(
    executor,
    repository,
    recorder,
):
    batch = await executor.preflight(
        [
            _call("tc1", "write_file", {"path": "one"}),
            _call("tc2", "browser_click", {"target": "submit"}),
        ]
    )
    await repository.decide_approval_call(
        "tc1",
        ApprovalStatus.APPROVED,
        "u1",
    )

    result = await executor.resume(batch.batch_id, actor_id="u1")

    assert result.rejected_reason == "approval_pending"
    assert recorder.invocations == []


@pytest.mark.asyncio
async def test_rejected_approval_has_explicit_outcome_and_executes_nothing(
    executor,
    repository,
    recorder,
):
    batch = await executor.preflight(
        [_call("tc1", "browser_click", {"target": "submit"})]
    )
    await repository.decide_approval_call(
        "tc1",
        ApprovalStatus.REJECTED,
        "u1",
    )

    result = await executor.resume(batch.batch_id, actor_id="u1")

    assert result.rejected_reason == "approval_rejected"
    assert recorder.invocations == []


@pytest.mark.asyncio
async def test_all_arguments_are_validated_before_any_call_executes(
    executor,
    recorder,
):
    with pytest.raises(TypeError, match="path"):
        await executor.preflight(
            [
                _call("tc1", "write_file", {"path": "one"}),
                _call("tc2", "write_file", {}),
            ]
        )

    assert recorder.invocations == []


@pytest.mark.asyncio
async def test_capability_denial_happens_during_preflight_without_execution(
    recorder,
    repository,
):
    executor = ToolBatchExecutor(
        session_id="s1",
        tools=[_RecordingTool(recorder)],
        approval_repository=repository,
        capability_policy=CapabilityPolicy.for_mode(SessionMode.ASK),
    )

    with pytest.raises(CapabilityDeniedError, match="write_file") as exc_info:
        await executor.preflight(
            [_call("tc1", "write_file", {"path": "one"})]
        )

    # _resolve_tool_and_policy's denial is execution-layer: the batch
    # cleared assembly/exposure and was denied resolving the concrete call.
    assert exc_info.value.layer == "execution"
    assert exc_info.value.tool_name == "write_file"
    assert recorder.invocations == []


@pytest.mark.asyncio
async def test_fresh_executor_does_not_reexecute_consumed_batch(
    executor,
    repository,
    recorder,
):
    batch = await executor.preflight(
        [_call("tc1", "browser_click", {"target": "submit"})]
    )
    await repository.decide_approval_call(
        "tc1",
        ApprovalStatus.APPROVED,
        "u1",
    )
    first = await executor.resume(batch.batch_id, actor_id="u1")
    fresh = ToolBatchExecutor(
        session_id="s1",
        tools=[_RecordingTool(recorder)],
        approval_repository=repository,
        capability_policy=CapabilityPolicy.for_mode(SessionMode.AGENT),
    )

    second = await fresh.resume(batch.batch_id, actor_id="u1")

    assert first.rejected_reason is None
    assert second.rejected_reason == "approval_consumed"
    assert recorder.invocations == ["browser_click:submit"]


@pytest.mark.asyncio
async def test_resume_rejects_batch_from_another_session_before_consumption(
    executor,
    repository,
    recorder,
):
    batch = await executor.preflight(
        [_call("tc1", "browser_click", {"target": "submit"})]
    )
    await repository.decide_approval_call(
        "tc1",
        ApprovalStatus.APPROVED,
        "u1",
    )
    other_session = ToolBatchExecutor(
        session_id="s2",
        tools=[_RecordingTool(recorder)],
        approval_repository=repository,
        capability_policy=CapabilityPolicy.for_mode(SessionMode.AGENT),
    )

    result = await other_session.resume(batch.batch_id, actor_id="u1")

    assert result.rejected_reason == "approval_session_mismatch"
    assert repository.batches[batch.batch_id].status == ApprovalStatus.APPROVED
    assert recorder.invocations == []


@pytest.mark.asyncio
async def test_resume_rejects_arguments_changed_by_renormalization_before_claim(
    executor,
    repository,
    recorder,
):
    batch = await executor.preflight(
        [_call("tc1", "browser_click", {"target": "submit"})]
    )
    await repository.decide_approval_call(
        "tc1",
        ApprovalStatus.APPROVED,
        "u1",
    )
    stored = repository.batches[batch.batch_id]
    changed_args = {"target": "submit", "removed_parameter": True}
    changed_call = stored.calls[0].model_copy(
        update={
            "normalized_args": changed_args,
            "args_hash": hash_normalized_args(changed_args),
        }
    )
    repository.batches[batch.batch_id] = stored.model_copy(
        update={"calls": [changed_call]}
    )

    result = await executor.resume(batch.batch_id, actor_id="u1")

    assert result.rejected_reason == "approval_validation_failed"
    assert repository.batches[batch.batch_id].status == ApprovalStatus.APPROVED
    assert recorder.invocations == []


@pytest.mark.asyncio
async def test_shared_stateful_lock_serializes_calls_across_batches(
    recorder,
    repository,
):
    shared_lock = asyncio.Lock()
    tool_pack = _RecordingTool(recorder)
    first = ToolBatchExecutor(
        session_id="s1",
        tools=[tool_pack],
        approval_repository=repository,
        capability_policy=CapabilityPolicy.for_mode(SessionMode.AGENT),
        stateful_tool_lock=shared_lock,
    )
    second = ToolBatchExecutor(
        session_id="s2",
        tools=[tool_pack],
        approval_repository=repository,
        capability_policy=CapabilityPolicy.for_mode(SessionMode.AGENT),
        stateful_tool_lock=shared_lock,
    )
    first_batch = await first.preflight(
        [_call("tc1", "browser_click", {"target": "one", "delay": 0.02})]
    )
    second_batch = await second.preflight(
        [_call("tc2", "browser_click", {"target": "two", "delay": 0.02})]
    )
    await repository.decide_approval_call(
        "tc1",
        ApprovalStatus.APPROVED,
        "u1",
    )
    await repository.decide_approval_call(
        "tc2",
        ApprovalStatus.APPROVED,
        "u2",
    )

    await asyncio.gather(
        first.resume(first_batch.batch_id, actor_id="u1"),
        second.resume(second_batch.batch_id, actor_id="u2"),
    )

    assert recorder.max_parallel_by_group["browser"] == 1


# --- governance metrics wiring -------------------------------------------


@pytest.mark.asyncio
async def test_resume_records_consumed_outcome_on_atomic_claim(
    executor,
    repository,
    recorder,
):
    batch = await executor.preflight(
        [_call("tc1", "browser_click", {"target": "submit"})]
    )
    await repository.decide_approval_call("tc1", ApprovalStatus.APPROVED, "u1")
    before = _counter_value(
        "governance_approval_batches_total", {"outcome": "consumed"}
    )

    result = await executor.resume(batch.batch_id, actor_id="u1")

    after = _counter_value(
        "governance_approval_batches_total", {"outcome": "consumed"}
    )
    assert result.rejected_reason is None
    assert after - before == 1.0


@pytest.mark.asyncio
async def test_resume_does_not_double_count_consumed_on_repeat_call(
    executor,
    repository,
    recorder,
):
    batch = await executor.preflight(
        [_call("tc1", "browser_click", {"target": "submit"})]
    )
    await repository.decide_approval_call("tc1", ApprovalStatus.APPROVED, "u1")
    await executor.resume(batch.batch_id, actor_id="u1")
    fresh = ToolBatchExecutor(
        session_id="s1",
        tools=[_RecordingTool(recorder)],
        approval_repository=repository,
        capability_policy=CapabilityPolicy.for_mode(SessionMode.AGENT),
    )
    before = _counter_value(
        "governance_approval_batches_total", {"outcome": "consumed"}
    )

    second = await fresh.resume(batch.batch_id, actor_id="u1")

    after = _counter_value(
        "governance_approval_batches_total", {"outcome": "consumed"}
    )
    assert second.rejected_reason == "approval_consumed"
    assert after - before == 0.0


@pytest.mark.asyncio
async def test_resume_records_expired_outcome(
    executor,
    repository,
    recorder,
):
    batch = await executor.preflight(
        [_call("tc1", "browser_click", {"target": "submit"})]
    )
    repository.batches[batch.batch_id] = repository.batches[
        batch.batch_id
    ].model_copy(
        update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
    )
    before = _counter_value(
        "governance_approval_batches_total", {"outcome": "expired"}
    )

    result = await executor.resume(batch.batch_id, actor_id="u1")

    after = _counter_value(
        "governance_approval_batches_total", {"outcome": "expired"}
    )
    assert result.rejected_reason == "approval_expired"
    assert after - before == 1.0


@pytest.mark.asyncio
async def test_resume_records_rejected_outcome(
    executor,
    repository,
    recorder,
):
    batch = await executor.preflight(
        [_call("tc1", "browser_click", {"target": "submit"})]
    )
    await repository.decide_approval_call("tc1", ApprovalStatus.REJECTED, "u1")
    before = _counter_value(
        "governance_approval_batches_total", {"outcome": "rejected"}
    )

    result = await executor.resume(batch.batch_id, actor_id="u1")

    after = _counter_value(
        "governance_approval_batches_total", {"outcome": "rejected"}
    )
    assert result.rejected_reason == "approval_rejected"
    assert after - before == 1.0


@pytest.mark.asyncio
async def test_execute_call_records_ok_status_and_duration(
    executor,
    recorder,
):
    batch = await executor.preflight([_call("r1", "read_a", {})])
    before_count = _counter_value(
        "governance_tool_executions_total", {"tool": "read_a", "status": "ok"}
    )
    before_hist = REGISTRY.get_sample_value(
        "governance_tool_execution_seconds_count", {"tool": "read_a"}
    ) or 0.0

    result = await executor.execute(batch)

    after_count = _counter_value(
        "governance_tool_executions_total", {"tool": "read_a", "status": "ok"}
    )
    after_hist = REGISTRY.get_sample_value(
        "governance_tool_execution_seconds_count", {"tool": "read_a"}
    ) or 0.0
    assert result.calls[0].result.success is True
    assert after_count - before_count == 1.0
    assert after_hist - before_hist == 1.0


@pytest.mark.asyncio
async def test_execute_call_records_error_status(repository):
    class _FailingTool(BaseTool):
        name = "failing"

        @tool(
            name="boom",
            description="boom",
            parameters={},
            required=[],
            policy=READ_POLICY,
        )
        async def boom(self) -> str:
            raise RuntimeError("boom")

    executor = ToolBatchExecutor(
        session_id="s1",
        tools=[_FailingTool()],
        approval_repository=repository,
        capability_policy=CapabilityPolicy.for_mode(SessionMode.AGENT),
    )
    batch = await executor.preflight([_call("f1", "boom", {})])
    before = _counter_value(
        "governance_tool_executions_total", {"tool": "boom", "status": "error"}
    )

    result = await executor.execute(batch)

    after = _counter_value(
        "governance_tool_executions_total", {"tool": "boom", "status": "error"}
    )
    assert result.calls[0].result.success is False
    assert after - before == 1.0


@pytest.mark.asyncio
async def test_execute_call_records_denied_status_without_duration(
    recorder,
    repository,
):
    async def invoke_call(tool_pack, function_name, arguments):
        raise CapabilityDeniedError("denied at invoke time")

    executor = ToolBatchExecutor(
        session_id="s1",
        tools=[_RecordingTool(recorder)],
        approval_repository=repository,
        capability_policy=CapabilityPolicy.for_mode(SessionMode.AGENT),
        invoke_call=invoke_call,
    )
    batch = await executor.preflight([_call("r1", "read_a", {})])
    before_count = _counter_value(
        "governance_tool_executions_total",
        {"tool": "read_a", "status": "denied"},
    )
    before_hist = REGISTRY.get_sample_value(
        "governance_tool_execution_seconds_count", {"tool": "read_a"}
    ) or 0.0

    result = await executor.execute(batch)

    after_count = _counter_value(
        "governance_tool_executions_total",
        {"tool": "read_a", "status": "denied"},
    )
    after_hist = REGISTRY.get_sample_value(
        "governance_tool_execution_seconds_count", {"tool": "read_a"}
    ) or 0.0
    assert result.calls[0].result.success is False
    assert after_count - before_count == 1.0
    assert after_hist - before_hist == 0.0


@pytest.mark.asyncio
async def test_execute_call_denied_branch_calls_denial_audit_hook_exactly_once(
    recorder,
    repository,
):
    """Task 2: the denied branch in ``_execute_call`` must audit the denial
    (via the injected ``denial_audit_call`` hook, wired to
    ToolAuditRecorder.record_tool_denied by BaseAgent) exactly once, using
    the outer call's function name and the exception's enforcement layer —
    and leave the existing failure semantics (result.success is False)
    unchanged."""
    async def invoke_call(tool_pack, function_name, arguments):
        raise CapabilityDeniedError(
            "denied at invoke time", layer="assembly", tool_name="unrelated-nested-tool",
        )

    denial_calls: list[tuple[str, str, str]] = []

    async def denial_audit_call(tool_name: str, layer: str, reason: str) -> None:
        denial_calls.append((tool_name, layer, reason))

    executor = ToolBatchExecutor(
        session_id="s1",
        tools=[_RecordingTool(recorder)],
        approval_repository=repository,
        capability_policy=CapabilityPolicy.for_mode(SessionMode.AGENT),
        invoke_call=invoke_call,
        denial_audit_call=denial_audit_call,
    )
    batch = await executor.preflight([_call("r1", "read_a", {})])

    result = await executor.execute(batch)

    assert result.calls[0].result.success is False
    # tool_name is the outer prepared call's function name, not the (possibly
    # different) exception's own tool_name — the denial concerns *this* call.
    assert denial_calls == [("read_a", "assembly", "denied at invoke time")]


@pytest.mark.asyncio
async def test_execute_call_non_denied_failure_does_not_call_denial_audit_hook(
    repository,
):
    class _FailingTool(BaseTool):
        name = "failing"

        @tool(
            name="boom",
            description="boom",
            parameters={},
            required=[],
            policy=READ_POLICY,
        )
        async def boom(self) -> str:
            raise RuntimeError("boom")

    denial_calls: list[tuple[str, str, str]] = []

    async def denial_audit_call(tool_name: str, layer: str, reason: str) -> None:
        denial_calls.append((tool_name, layer, reason))

    executor = ToolBatchExecutor(
        session_id="s1",
        tools=[_FailingTool()],
        approval_repository=repository,
        capability_policy=CapabilityPolicy.for_mode(SessionMode.AGENT),
        denial_audit_call=denial_audit_call,
    )
    batch = await executor.preflight([_call("f1", "boom", {})])

    result = await executor.execute(batch)

    assert result.calls[0].result.success is False
    assert denial_calls == []
