#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Retry behavior is governed by a tool call's idempotency policy."""
import asyncio
import errno

import pytest

from app.domain.models.tool_execution import ToolExecutionStatus
from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)
from app.domain.services.agents.tool_batch_executor import ToolBatchExecutor
from app.domain.services.tools.base import BaseTool, tool


SAFE_READ_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.CODE_READ,
    effect=ToolEffect.READ_ONLY,
    idempotency=ToolIdempotency.SAFE,
    approval=ApprovalMode.NEVER,
)
NON_IDEMPOTENT_WRITE_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.WORKSPACE_WRITE,
    idempotency=ToolIdempotency.NON_IDEMPOTENT,
    approval=ApprovalMode.NEVER,
)
UNKNOWN_WRITE_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.EXTERNAL_WRITE,
    idempotency=ToolIdempotency.UNKNOWN,
    approval=ApprovalMode.NEVER,
)
KEYED_WRITE_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.EXTERNAL_WRITE,
    idempotency=ToolIdempotency.IDEMPOTENT_WITH_KEY,
    approval=ApprovalMode.NEVER,
)


class _ApprovalRepository:
    def __init__(self) -> None:
        self.batches = {}

    async def save_approval_batch(self, batch):
        self.batches[batch.id] = batch

    async def get_pending_approval_batch(self, session_id):
        return None

    async def get_approval_batch(self, batch_id):
        return self.batches.get(batch_id)

    async def decide_approval_call(self, tool_call_id, status, decided_by):
        return None

    async def consume_approval_batch(self, batch_id):
        batch = self.batches.get(batch_id)
        if batch is None or batch.status.value != "approved":
            return batch
        consumed = batch.model_copy(update={"status": "consumed"})
        self.batches[batch_id] = consumed
        return consumed.model_copy(update={"execution_claimed": True})


class _RetryTool(BaseTool):
    name = "retry"

    def __init__(self) -> None:
        super().__init__()
        self.non_idempotent_calls = 0
        self.unknown_calls = 0
        self.read_calls = 0
        self.authorization_calls = 0
        self.unkeyed_policy_calls = 0
        self.kwargs_policy_calls = 0
        self.kwargs_received = []
        self.missing_file_calls = 0
        self.connection_errno_calls = 0
        self.cancelled_calls = 0
        self.received_keys: list[str] = []

    @tool(
        name="non_idempotent_timeout",
        description="times out after starting a write",
        parameters={},
        required=[],
        policy=NON_IDEMPOTENT_WRITE_POLICY,
    )
    async def non_idempotent_timeout(self) -> str:
        self.non_idempotent_calls += 1
        await asyncio.sleep(0.02)
        return "late write"

    @tool(
        name="unknown_timeout",
        description="times out after starting an unknown external write",
        parameters={},
        required=[],
        policy=UNKNOWN_WRITE_POLICY,
    )
    async def unknown_timeout(self) -> str:
        self.unknown_calls += 1
        await asyncio.sleep(0.02)
        return "late write"

    @tool(
        name="flaky_read",
        description="fails once with a transient transport failure",
        parameters={},
        required=[],
        policy=SAFE_READ_POLICY,
    )
    async def flaky_read(self) -> str:
        self.read_calls += 1
        if self.read_calls == 1:
            raise ConnectionError("connection reset")
        return "read result"

    @tool(
        name="unauthorized_read",
        description="rejects an authorization failure",
        parameters={},
        required=[],
        policy=SAFE_READ_POLICY,
    )
    async def unauthorized_read(self) -> str:
        self.authorization_calls += 1
        raise PermissionError("authorization denied")

    @tool(
        name="flaky_keyed_write",
        description="fails once before completing a keyed write",
        parameters={"idempotency_key": {"type": "string"}},
        required=[],
        policy=KEYED_WRITE_POLICY,
    )
    async def flaky_keyed_write(self, idempotency_key: str | None = None) -> str:
        assert idempotency_key is not None
        self.received_keys.append(idempotency_key)
        if len(self.received_keys) == 1:
            raise ConnectionError("connection reset")
        return "write result"

    @tool(
        name="schema_only_keyed_write",
        description="declares a key but silently cannot receive it",
        parameters={"idempotency_key": {"type": "string"}},
        required=[],
        policy=KEYED_WRITE_POLICY,
    )
    async def schema_only_keyed_write(self) -> str:
        self.unkeyed_policy_calls += 1
        raise ConnectionError("connection reset")

    @tool(
        name="kwargs_keyed_write",
        description="appears to accept arbitrary arguments but BaseTool drops keys",
        parameters={"idempotency_key": {"type": "string"}},
        required=[],
        policy=KEYED_WRITE_POLICY,
    )
    async def kwargs_keyed_write(self, **kwargs) -> str:
        self.kwargs_policy_calls += 1
        self.kwargs_received.append(kwargs)
        raise ConnectionError("connection reset")

    @tool(
        name="missing_file_read",
        description="reports a deterministic filesystem error",
        parameters={},
        required=[],
        policy=SAFE_READ_POLICY,
    )
    async def missing_file_read(self) -> str:
        self.missing_file_calls += 1
        raise FileNotFoundError("missing user-selected file")

    @tool(
        name="connection_errno_read",
        description="reports a retryable connection reset errno",
        parameters={},
        required=[],
        policy=SAFE_READ_POLICY,
    )
    async def connection_errno_read(self) -> str:
        self.connection_errno_calls += 1
        if self.connection_errno_calls == 1:
            raise OSError(errno.ECONNRESET, "connection reset")
        return "read result"

    @tool(
        name="cancelled_read",
        description="is cancelled by its caller",
        parameters={},
        required=[],
        policy=SAFE_READ_POLICY,
    )
    async def cancelled_read(self) -> str:
        self.cancelled_calls += 1
        raise asyncio.CancelledError()


def _call(call_id: str, name: str) -> dict:
    return {
        "id": call_id,
        "function": {"name": name, "arguments": {}},
    }


@pytest.fixture
def retry_tool():
    return _RetryTool()


@pytest.fixture
def executor(retry_tool):
    return ToolBatchExecutor(
        session_id="session-1",
        tools=[retry_tool],
        approval_repository=_ApprovalRepository(),
        max_attempts=2,
        timeout_seconds=0.005,
        retry_interval=0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "counter"),
    [
        ("non_idempotent_timeout", "non_idempotent_calls"),
        ("unknown_timeout", "unknown_calls"),
    ],
)
async def test_non_replayable_timeout_is_not_retried_and_has_unknown_outcome(
    executor,
    retry_tool,
    tool_name,
    counter,
):
    """Removing the no-retry branch would call an uncertain write twice."""
    result = await executor.invoke(_call("call-timeout", tool_name))

    assert getattr(retry_tool, counter) == 1
    assert result.status is ToolExecutionStatus.OUTCOME_UNKNOWN
    assert len(result.attempts) == 1
    assert result.attempts[0].status is ToolExecutionStatus.OUTCOME_UNKNOWN


@pytest.mark.asyncio
async def test_safe_read_retries_only_a_transient_failure_with_bounded_attempts(
    executor,
    retry_tool,
):
    """Removing transient retry handling would leave a recoverable read failed."""
    result = await executor.invoke(_call("call-read", "flaky_read"))

    assert retry_tool.read_calls == 2
    assert result.success is True
    assert result.status is ToolExecutionStatus.SUCCESS
    assert [attempt.attempt_number for attempt in result.attempts] == [1, 2]


@pytest.mark.asyncio
async def test_safe_read_never_retries_an_authorization_failure(
    executor,
    retry_tool,
):
    """Classifying authorization errors as transport failures would repeat a denial."""
    result = await executor.invoke(_call("call-denied", "unauthorized_read"))

    assert retry_tool.authorization_calls == 1
    assert result.success is False
    assert len(result.attempts) == 1
    assert result.attempts[0].transient_failure is False


@pytest.mark.asyncio
async def test_keyed_write_reuses_one_stable_idempotency_key_for_every_attempt(
    executor,
    retry_tool,
):
    """Regenerating the key would allow a transport retry to duplicate a write."""
    result = await executor.invoke(_call("call-keyed", "flaky_keyed_write"))

    assert result.success is True
    assert len(retry_tool.received_keys) == 2
    assert len(set(retry_tool.received_keys)) == 1
    assert retry_tool.received_keys[0] == (
        "dd4c79b534b1193acd66ca06c44da54d743aacadd8bdffc066ba4600be16fe3f"
    )
    assert [attempt.idempotency_key for attempt in result.attempts] == [
        retry_tool.received_keys[0],
        retry_tool.received_keys[0],
    ]


@pytest.mark.asyncio
async def test_keyed_policy_without_callable_key_contract_executes_once(
    executor,
    retry_tool,
):
    """Retrying this call would replay a write after BaseTool strips the key."""
    result = await executor.invoke(_call("call-schema-only", "schema_only_keyed_write"))

    assert retry_tool.unkeyed_policy_calls == 1
    assert result.success is False
    assert len(result.attempts) == 1
    assert result.attempts[0].idempotency_key is None


@pytest.mark.asyncio
async def test_keyed_policy_with_only_kwargs_executes_once_without_claiming_key_delivery(
    executor,
    retry_tool,
):
    """Accepting VAR_KEYWORD here would replay a write after filtering drops its key."""
    result = await executor.invoke(_call("call-kwargs", "kwargs_keyed_write"))

    assert retry_tool.kwargs_policy_calls == 1
    assert retry_tool.kwargs_received == [{}]
    assert result.success is False
    assert len(result.attempts) == 1
    assert result.attempts[0].idempotency_key is None


@pytest.mark.asyncio
async def test_safe_read_does_not_retry_deterministic_filesystem_errors(
    executor,
    retry_tool,
):
    """Treating all OSErrors as transport errors would repeat invalid input."""
    result = await executor.invoke(_call("call-missing", "missing_file_read"))

    assert retry_tool.missing_file_calls == 1
    assert result.success is False
    assert result.attempts[0].transient_failure is False


@pytest.mark.asyncio
async def test_safe_read_retries_only_allowlisted_connection_errno(
    executor,
    retry_tool,
):
    """Removing the narrow connection errno classification loses a recoverable read."""
    result = await executor.invoke(_call("call-errno", "connection_errno_read"))

    assert retry_tool.connection_errno_calls == 2
    assert result.success is True


@pytest.mark.asyncio
async def test_cancellation_is_propagated_without_a_retry(executor, retry_tool):
    """Catching cancellation would convert caller shutdown into a duplicate call."""
    with pytest.raises(asyncio.CancelledError):
        await executor.invoke(_call("call-cancelled", "cancelled_read"))

    assert retry_tool.cancelled_calls == 1
