#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Executable cross-layer invariants for shared agent governance."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domain.models.agent_runtime_settings import AgentRuntimeSettings
from app.domain.models.app_config import AgentConfig
from app.domain.models.codebase import SessionMode
from app.domain.models.event import MessageEvent, SessionStatusEvent
from app.domain.models.llm_model import ModelCapabilities
from app.domain.models.memory import Memory
from app.domain.models.run_outcome import RunOutcome
from app.domain.models.session import SessionStatus
from app.domain.models.tool_approval import ApprovalStatus
from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)
from app.domain.services.agent.event_emitter import AgentEventEmitter
from app.domain.services.agent_task_runner import AgentTaskRunner
from app.domain.services.agents.tool_batch_executor import ToolBatchExecutor
from app.domain.services.flows.code_ask_flow import CodeAskFlow
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.capability_policy import CapabilityDeniedError
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.subagent import SubAgentTool


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
GATED_EXTERNAL_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.EXTERNAL_WRITE,
    idempotency=ToolIdempotency.NON_IDEMPOTENT,
    approval=ApprovalMode.ALWAYS,
    concurrency_group="integration",
)


class _NoopObservability:
    def record_agent_cancel(self, _session_id: str) -> None:
        return None

    def record_llm_tokens(self, *_args, **_kwargs) -> None:
        return None

    def record_agent_step(self, *_args, **_kwargs) -> None:
        return None

    def create_agent_tracer(self, *_args, **_kwargs):
        return SimpleNamespace(span=lambda _name: _NullContext())


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _MemorySessionRepository:
    def __init__(self) -> None:
        self.memory = Memory()

    async def get_memory(self, _session_id: str, _agent_name: str) -> Memory:
        return self.memory

    async def save_memory(
        self,
        _session_id: str,
        _agent_name: str,
        memory: Memory,
    ) -> None:
        self.memory = memory


class _MemoryUow:
    def __init__(self, session: _MemorySessionRepository) -> None:
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _ContractAwareAskLLM:
    """External-model stub that follows only contracts present in the prompt."""

    model_name = "governance-contract-model"
    supports_multimodal = False

    def __init__(self) -> None:
        self.exposed_tool_names: set[str] = set()
        self.system_prompt = ""

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities()

    async def stream_invoke(self, *, messages, tools, **_kwargs):
        self.exposed_tool_names = {
            item["function"]["name"]
            for item in tools
        }
        self.system_prompt = next(
            (
                str(message.get("content") or "")
                for message in messages
                if message.get("role") == "system"
            ),
            "",
        )
        if (
            "副作用" in self.system_prompt
            and "切换到 Agent 模式" in self.system_prompt
        ):
            yield {
                "content": (
                    "Ask 模式不会执行创建文件、外部写入或委派；"
                    "请切换到 Agent 模式。"
                )
            }
        else:
            yield {"content": "Ask 模式无法执行该请求。"}


@pytest.mark.asyncio
async def test_ask_has_zero_side_effects_across_delegation_and_integration():
    side_effects: list[str] = []
    mcp_tool = MCPTool(MagicMock())
    mcp_tool.register_schema(
        "create_ticket",
        schema={
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
        policy=GATED_EXTERNAL_POLICY,
    )

    async def _run_subagent(**_kwargs) -> str:
        side_effects.append("delegate_subtask")
        return "delegated"

    subagent_tool = SubAgentTool(_run_subagent)
    memory_repository = _MemorySessionRepository()
    llm = _ContractAwareAskLLM()
    flow = CodeAskFlow(
        uow_factory=lambda: _MemoryUow(memory_repository),
        llm=llm,
        agent_config=AgentConfig(max_iterations=2, max_retries=2),
        session_id="ask-session",
        json_parser=MagicMock(),
        browser=MagicMock(),
        sandbox=MagicMock(),
        search_engine=MagicMock(),
        mcp_tool=mcp_tool,
        a2a_tool=A2ATool(MagicMock()),
        observability_port=_NoopObservability(),
        runtime_settings=AgentRuntimeSettings(),
        extra_tools=[subagent_tool],
    )

    events = [
        event
        async for event in flow.invoke(
            SimpleNamespace(
                message=(
                    "请委派子任务创建文件，并通过工单集成创建 ticket"
                ),
                vision_attachments=[],
            )
        )
    ]

    assert flow.outcome == RunOutcome.succeeded()
    assert not {"delegate_subtask", "create_ticket"} & llm.exposed_tool_names
    assert side_effects == []
    final_message = next(
        event.message
        for event in reversed(events)
        if isinstance(event, MessageEvent)
    )
    assert "Agent 模式" in final_message

    governed_by_name = {
        tool_pack.name: tool_pack
        for tool_pack in flow._agent._tools
    }
    with pytest.raises(CapabilityDeniedError):
        await governed_by_name["subagent"].invoke(
            "delegate_subtask",
            goal="create a file",
        )
    with pytest.raises(CapabilityDeniedError):
        await governed_by_name["mcp"].invoke(
            "create_ticket",
            title="external write",
        )
    assert side_effects == []


class _MixedBatchTool(BaseTool):
    name = "mixed"

    def __init__(self, side_effects: list[str]) -> None:
        super().__init__()
        self.side_effects = side_effects

    @tool(
        name="read_context",
        description="read",
        parameters={},
        required=[],
        policy=READ_POLICY,
    )
    async def read_context(self):
        self.side_effects.append("read_context")
        return "context"

    @tool(
        name="write_workspace",
        description="write",
        parameters={"path": {"type": "string"}},
        required=["path"],
        policy=WRITE_POLICY,
    )
    async def write_workspace(self, path: str):
        self.side_effects.append(f"write_workspace:{path}")
        return "written"

    @tool(
        name="create_ticket",
        description="external write",
        parameters={"title": {"type": "string"}},
        required=["title"],
        policy=GATED_EXTERNAL_POLICY,
    )
    async def create_ticket(self, title: str):
        self.side_effects.append(f"create_ticket:{title}")
        return "created"


class _ApprovalRepository:
    def __init__(self) -> None:
        self.batches = {}

    async def save_approval_batch(self, batch) -> None:
        self.batches[batch.id] = batch

    async def get_pending_approval_batch(self, session_id):
        return next(
            (
                batch
                for batch in self.batches.values()
                if batch.session_id == session_id
                and batch.status == ApprovalStatus.PENDING
            ),
            None,
        )

    async def get_approval_batch(self, batch_id):
        return self.batches.get(batch_id)


def _tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "function": {"name": name, "arguments": arguments},
    }


@pytest.mark.asyncio
async def test_mixed_gated_batch_persists_every_call_before_any_execution():
    side_effects: list[str] = []
    repository = _ApprovalRepository()
    executor = ToolBatchExecutor(
        session_id="batch-session",
        tools=[_MixedBatchTool(side_effects)],
        approval_repository=repository,
    )
    prepared = await executor.preflight([
        _tool_call("call-1", "read_context", {}),
        _tool_call("call-2", "write_workspace", {"path": "report.md"}),
        _tool_call("call-3", "create_ticket", {"title": "Publish report"}),
    ])

    result = await executor.execute(prepared)

    assert result.waiting is True
    assert side_effects == []
    persisted = repository.batches[prepared.batch_id]
    assert persisted.status == ApprovalStatus.PENDING
    assert [
        (call.ordinal, call.tool_call_id, call.tool_name, call.status)
        for call in persisted.calls
    ] == [
        (0, "call-1", "read_context", ApprovalStatus.APPROVED),
        (1, "call-2", "write_workspace", ApprovalStatus.APPROVED),
        (2, "call-3", "create_ticket", ApprovalStatus.PENDING),
    ]
    payload = persisted.model_dump(mode="json")
    assert set(payload) == {
        "id",
        "session_id",
        "status",
        "expires_at",
        "created_at",
        "decided_at",
        "calls",
    }
    assert set(payload["calls"][0]) == {
        "id",
        "batch_id",
        "tool_call_id",
        "ordinal",
        "tool_name",
        "normalized_args",
        "args_hash",
        "capability",
        "effect",
        "idempotency",
        "approval",
        "concurrency_group",
        "status",
        "decided_by",
        "decided_at",
    }


class _EventStore:
    def __init__(self) -> None:
        self.records: list[tuple[int, SessionStatusEvent, dict]] = []
        self._lock = asyncio.Lock()
        self.session = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def claim_session_status_event(
        self,
        _session_id: str,
        event: SessionStatusEvent,
        payload: dict,
    ) -> bool:
        async with self._lock:
            same_epoch = [
                record
                for record in self.records
                if record[1].run_epoch_id == event.run_epoch_id
            ]
            if event.status == SessionStatus.RUNNING.value:
                if any(
                    record[1].status == SessionStatus.RUNNING.value
                    for record in same_epoch
                ):
                    return False
            elif (
                not any(
                    record[1].status == SessionStatus.RUNNING.value
                    for record in same_epoch
                )
                or any(
                    record[1].status
                    in {
                        SessionStatus.WAITING.value,
                        SessionStatus.COMPLETED.value,
                        SessionStatus.CANCELLED.value,
                        SessionStatus.FAILED.value,
                    }
                    for record in same_epoch
                )
            ):
                return False
            self.records.append(
                (int(event.id), event.model_copy(deep=True), dict(payload))
            )
            return True

    async def list_events(
        self,
        _session_id: str,
        *,
        before=None,
        limit=200,
        latest=False,
    ):
        records = [
            (seq, event)
            for seq, event, _payload in sorted(self.records)
            if before is None or seq < before
        ]
        return records[-limit:] if latest or before is not None else records[:limit]

    async def add_event_payloads(self, _session_id: str, payloads) -> None:
        for event, payload in payloads:
            self.records.append((int(event.id), event, payload))


class _Sequence:
    def __init__(self) -> None:
        self.value = 0

    async def allocate(self) -> int:
        self.value += 1
        return self.value


class _OutputStream:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    async def put(self, payload: str) -> str:
        self.payloads.append(payload)
        return f"stream-{len(self.payloads)}"


class _TaskState:
    async def set_output_seq_cursor(self, *_args) -> None:
        return None

    async def set_run_reconciliation(
        self,
        _task_id: str,
        run_generation: int,
        _run_epoch_id: str,
        _outcome: dict,
    ) -> bool:
        return run_generation == 1


def _terminal_runner(store: _EventStore):
    task_state = _TaskState()
    runner = AgentTaskRunner.__new__(AgentTaskRunner)
    runner._session_id = "terminal-session"
    runner._run_epoch_id = "task-1:input-1"
    runner._unpersisted_run_reconciliation = None
    runner._unpersisted_reconciliation_message_id = None
    runner._terminal_session_status = None
    runner._on_session_terminal_callback = None
    runner._task_state_port = task_state
    runner._event_emitter = AgentEventEmitter(
        session_id=runner._session_id,
        uow_factory=lambda: store,
        event_sequence=_Sequence(),
        task_state_port=task_state,
    )
    task = SimpleNamespace(
        id="task-1",
        run_generation=1,
        output_stream=_OutputStream(),
    )
    return runner, task


@pytest.mark.parametrize(
    "outcome",
    [
        RunOutcome.failed("flow failed", code="FLOW_FAILED"),
        RunOutcome.cancelled("operator cancelled", code="OPERATOR_CANCELLED"),
        RunOutcome.waiting(),
    ],
    ids=["failed", "cancelled", "waiting"],
)
@pytest.mark.asyncio
async def test_each_non_success_run_persists_and_publishes_one_terminal(outcome):
    store = _EventStore()
    runner, task = _terminal_runner(store)
    assert await runner._emit_session_status(
        task,
        SessionStatus.RUNNING,
    )

    selected = await runner._finalize_run_outcome(task, outcome)
    late_completed = await runner._apply_run_outcome(
        task,
        RunOutcome.succeeded(),
    )

    assert selected == outcome
    assert late_completed is False
    persisted_terminal = [
        event
        for _seq, event, _payload in store.records
        if event.status
        in {
            SessionStatus.WAITING.value,
            SessionStatus.COMPLETED.value,
            SessionStatus.CANCELLED.value,
            SessionStatus.FAILED.value,
        }
    ]
    published_terminal = [
        json.loads(payload)
        for payload in task.output_stream.payloads
        if json.loads(payload).get("status")
        in {
            SessionStatus.WAITING.value,
            SessionStatus.COMPLETED.value,
            SessionStatus.CANCELLED.value,
            SessionStatus.FAILED.value,
        }
    ]
    assert len(persisted_terminal) == 1
    assert len(published_terminal) == 1
    assert persisted_terminal[0].outcome == outcome
