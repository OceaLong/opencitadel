#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.domain.models.event import ApprovalEvent, MessageEvent, ToolEvent, WaitEvent
from app.domain.models.message import Message
from app.domain.models.plan import ExecutionStatus, Plan, Step
from app.domain.models.tool_approval import ApprovalStatus
from app.domain.models.tool_execution import ToolExecutionStatus
from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)
from app.domain.services.agents.base import BaseAgent
from app.domain.services.agents.react import ReActAgent
from app.domain.services.agents.tool_batch_executor import ToolBatchExecutor
from app.domain.services.tools.base import BaseTool, PolicyBoundTool, tool
from tests.app.domain.services.agents.conftest import (
    agent_test_observability_port,
    agent_test_runtime_settings,
)


WRITE = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.WORKSPACE_WRITE,
    idempotency=ToolIdempotency.NON_IDEMPOTENT,
    approval=ApprovalMode.NEVER,
    concurrency_group="filesystem",
)
GATED = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.INTERACTIVE,
    idempotency=ToolIdempotency.NON_IDEMPOTENT,
    approval=ApprovalMode.ALWAYS,
    concurrency_group="browser",
)
CHANGED_EFFECT = GATED.model_copy(update={"effect": ToolEffect.EXTERNAL_WRITE})
CHANGED_CAPABILITY = GATED.model_copy(
    update={"capability": ToolCapability.GENERATION}
)
LEGACY_SAFE_READ = ToolExecutionPolicy(
    capability=ToolCapability.CODE_READ,
    effect=ToolEffect.READ_ONLY,
    idempotency=ToolIdempotency.SAFE,
    approval=ApprovalMode.NEVER,
)
LEGACY_KEYED_WRITE = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.EXTERNAL_WRITE,
    idempotency=ToolIdempotency.IDEMPOTENT_WITH_KEY,
    approval=ApprovalMode.NEVER,
)


class _Tool(BaseTool):
    name = "effects"

    def __init__(self):
        super().__init__()
        self.invocations = []

    @tool(
        name="write_file",
        description="write",
        parameters={"path": {"type": "string"}},
        required=["path"],
        policy=WRITE,
    )
    async def write_file(self, path: str):
        self.invocations.append(("write_file", path))
        return "written"

    @tool(
        name="browser_click",
        description="click",
        parameters={"target": {"type": "string"}},
        required=["target"],
        policy=GATED,
    )
    async def browser_click(self, target: str):
        self.invocations.append(("browser_click", target))
        return "clicked"


class _ChangedEffectTool(_Tool):
    @tool(
        name="browser_click",
        description="click",
        parameters={"target": {"type": "string"}},
        required=["target"],
        policy=CHANGED_EFFECT,
    )
    async def browser_click(self, target: str):
        return await super().browser_click(target)


class _ChangedCapabilityTool(_Tool):
    @tool(
        name="browser_click",
        description="click",
        parameters={"target": {"type": "string"}},
        required=["target"],
        policy=CHANGED_CAPABILITY,
    )
    async def browser_click(self, target: str):
        return await super().browser_click(target)


class _ChangedSignatureTool(_Tool):
    @tool(
        name="browser_click",
        description="click",
        parameters={
            "target": {"type": "string"},
            "confirmation": {"type": "string"},
        },
        required=["target", "confirmation"],
        policy=GATED,
    )
    async def browser_click(self, target: str, confirmation: str):
        self.invocations.append(("browser_click", target, confirmation))
        return "clicked"


class _LegacyRetryTool(BaseTool):
    name = "legacy-retry"

    def __init__(self):
        super().__init__()
        self.safe_timeout_calls = 0
        self.keyed_calls = 0
        self.received_keys = []

    @tool(
        name="legacy_safe_timeout",
        description="retries a read after a timeout",
        parameters={},
        required=[],
        policy=LEGACY_SAFE_READ,
    )
    async def legacy_safe_timeout(self):
        self.safe_timeout_calls += 1
        if self.safe_timeout_calls == 1:
            raise asyncio.TimeoutError()
        return "read complete"

    @tool(
        name="legacy_keyed_write",
        description="retries a keyed write after a connection error",
        parameters={"idempotency_key": {"type": "string"}},
        required=[],
        policy=LEGACY_KEYED_WRITE,
    )
    async def legacy_keyed_write(self, idempotency_key: str | None = None):
        assert idempotency_key is not None
        self.keyed_calls += 1
        self.received_keys.append(idempotency_key)
        if self.keyed_calls == 1:
            raise ConnectionError("connection reset")
        return "write complete"


class _GovernanceRepository:
    def __init__(self):
        self.batch = None

    async def save_approval_batch(self, batch):
        self.batch = batch

    async def get_pending_approval_batch(self, session_id):
        return self.batch

    async def get_approval_batch(self, batch_id):
        if self.batch is None or self.batch.id != batch_id:
            return None
        return self.batch

    async def decide_approval_call(self, tool_call_id, status, decided_by):
        calls = list(self.batch.calls)
        index = next(
            index
            for index, call in enumerate(calls)
            if call.tool_call_id == tool_call_id
        )
        calls[index] = calls[index].model_copy(
            update={
                "status": status,
                "decided_by": decided_by,
                "decided_at": datetime.now(timezone.utc),
            }
        )
        batch_status = ApprovalStatus.PENDING
        if all(call.status != ApprovalStatus.PENDING for call in calls):
            batch_status = (
                ApprovalStatus.REJECTED
                if any(
                    call.status == ApprovalStatus.REJECTED
                    for call in calls
                )
                else ApprovalStatus.APPROVED
            )
        self.batch = self.batch.model_copy(
            update={"calls": calls, "status": batch_status}
        )
        return calls[index]

    async def consume_approval_batch(self, batch_id):
        if self.batch.status == ApprovalStatus.APPROVED:
            self.batch = self.batch.model_copy(
                update={"status": ApprovalStatus.CONSUMED}
            )
            return self.batch.model_copy(
                update={"execution_claimed": True}
            )
        return self.batch


class _SessionRepository:
    def __init__(self):
        self.session = SimpleNamespace(
            id="s1",
            owner_user_id="u1",
            team_id=None,
            pending_metadata={"visited_domains": ["example.com"]},
            pending_phase=None,
        )

    async def get_by_id(self, session_id):
        return self.session

    async def set_pending_metadata(self, session_id, metadata):
        self.session.pending_metadata = metadata

    async def set_pending_phase(self, session_id, phase):
        self.session.pending_phase = phase


class _AuditRepository:
    def __init__(self):
        self.items = []

    async def add(self, log):
        self.items.append(log)


class _Uow:
    def __init__(self):
        self.resource_governance = _GovernanceRepository()
        self.session = _SessionRepository()
        self.audit = _AuditRepository()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        return None


class _LLM:
    model_name = "test-model"
    supports_multimodal = False


class _Parser:
    async def invoke(self, value):
        raise AssertionError(f"unexpected JSON parse: {value}")


class _Agent(BaseAgent):
    name = "test"

    async def _invoke_llm(self, *args, **kwargs):
        self._last_llm_message = {"content": "done"}
        yield MessageEvent(message="done")


class _ResumeAgent(ReActAgent):
    name = "react-test"

    async def continue_tool_iteration_loop(self, *, inject_tool_messages=None, **kwargs):
        self.injected_tool_messages = inject_tool_messages
        yield MessageEvent(message="continued")

    async def _handle_execute_event(self, event, step):
        yield event


def _tool_call(call_id, name, arguments):
    return {
        "id": call_id,
        "function": {"name": name, "arguments": arguments},
    }


@pytest.mark.asyncio
async def test_base_agent_preflights_whole_batch_before_gated_execution():
    uow = _Uow()
    effect_tool = _Tool()
    agent = _Agent(
        uow_factory=lambda: uow,
        session_id="s1",
        agent_config=SimpleNamespace(
            max_retries=1,
            max_iterations=1,
            tool_result_max_chars=8000,
        ),
        llm=_LLM(),
        json_parser=_Parser(),
        tools=[effect_tool],
        observability_port=agent_test_observability_port(),
        runtime_settings=agent_test_runtime_settings(),
    )
    message = {
        "tool_calls": [
            _tool_call("tc1", "write_file", {"path": "one"}),
            _tool_call("tc2", "browser_click", {"target": "submit"}),
        ]
    }

    events = [
        event
        async for event in agent._run_tool_iteration_loop(
            message,
            None,
            emit_deltas=False,
            response_schema=None,
        )
    ]

    approvals = [
        event for event in events if isinstance(event, ApprovalEvent)
    ]
    assert len(approvals) == 1
    approval = approvals[0]
    assert isinstance(events[-1], WaitEvent)
    assert effect_tool.invocations == []
    assert approval.approval_id == uow.resource_governance.batch.id
    assert [
        item["tool_call_id"] for item in approval.payload["calls"]
    ] == ["tc1", "tc2"]
    assert approval.payload["tool_call_id"] == "tc2"
    assert approval.payload["tool_name"] == "browser_click"
    assert approval.payload["args"] == {"target": "submit"}
    assert uow.session.session.pending_phase == "tool_approval"
    assert (
        uow.session.session.pending_metadata["approval_batch_id"]
        == uow.resource_governance.batch.id
    )
    assert "pending_tool_call" not in uow.session.session.pending_metadata


@pytest.mark.asyncio
async def test_preflight_capability_denial_records_audit_and_metric_once():
    """Task 2: when preflight() raises CapabilityDeniedError (e.g. a policy-
    filtered PolicyBoundTool reports has_tool() == False for a requested
    call), the tool-iteration loop must record exactly one
    ``agent_tool_denied`` audit entry + one policy-denial metric tick, while
    leaving the existing "whole batch blocked" failure semantics unchanged
    (still one failed ToolEvent per requested call, no WaitEvent/approval)."""
    from prometheus_client import REGISTRY

    from app.domain.models.codebase import SessionMode
    from app.domain.services.tools.capability_policy import CapabilityPolicy

    def _counter_value(name, labels):
        return REGISTRY.get_sample_value(name, labels) or 0.0

    uow = _Uow()
    # ASK mode filters out WORKSPACE_WRITE-effect tools, so this pack's
    # has_tool("write_file") is False -> _resolve_tool_and_policy raises
    # CapabilityDeniedError(layer="execution", tool_name="write_file").
    policy_bound_tool = PolicyBoundTool(_Tool(), CapabilityPolicy.for_mode(SessionMode.ASK))
    agent = _Agent(
        uow_factory=lambda: uow,
        session_id="s1",
        agent_config=SimpleNamespace(
            max_retries=1,
            max_iterations=1,
            tool_result_max_chars=8000,
        ),
        llm=_LLM(),
        json_parser=_Parser(),
        tools=[policy_bound_tool],
        observability_port=agent_test_observability_port(),
        runtime_settings=agent_test_runtime_settings(gate_profile="strict"),
    )
    message = {"tool_calls": [_tool_call("tc1", "write_file", {"path": "one"})]}
    before = _counter_value(
        "governance_policy_denials_total",
        {"layer": "execution", "tool": "write_file"},
    )

    events = [
        event
        async for event in agent._run_tool_iteration_loop(
            message, None, emit_deltas=False, response_schema=None,
        )
    ]

    after = _counter_value(
        "governance_policy_denials_total",
        {"layer": "execution", "tool": "write_file"},
    )
    assert after - before == 1.0
    assert not any(isinstance(event, ApprovalEvent) for event in events)
    tool_events = [event for event in events if isinstance(event, ToolEvent)]
    assert len(tool_events) == 1
    assert tool_events[0].function_result.success is False
    assert len(uow.audit.items) == 1
    denial_log = uow.audit.items[0]
    assert denial_log.action == "agent_tool_denied"
    assert denial_log.metadata["tool"] == "write_file"
    assert denial_log.metadata["layer"] == "execution"
    assert denial_log.metadata["gate_profile"] == "strict"


@pytest.mark.asyncio
async def test_react_resumes_complete_persisted_approval_batch():
    uow = _Uow()
    effect_tool = _Tool()
    agent = _ResumeAgent(
        uow_factory=lambda: uow,
        session_id="s1",
        agent_config=SimpleNamespace(
            max_retries=1,
            max_iterations=1,
            tool_result_max_chars=8000,
        ),
        llm=_LLM(),
        json_parser=_Parser(),
        tools=[effect_tool],
        observability_port=agent_test_observability_port(),
        runtime_settings=agent_test_runtime_settings(),
    )
    batch = await agent._get_tool_batch_executor().preflight(
        [_tool_call("tc1", "browser_click", {"target": "submit"})]
    )
    session = uow.session.session
    session.owner_user_id = "u1"
    session.pending_phase = "tool_approval"
    session.pending_metadata = {"approval_batch_id": batch.batch_id}

    events = [
        event
        async for event in agent._resume_tool_approval(
            session,
            Message(message="approve"),
            Step(description="approve tool"),
        )
    ]

    assert effect_tool.invocations == [("browser_click", "submit")]
    assert any(isinstance(event, ToolEvent) for event in events)
    assert session.pending_phase is None
    assert session.pending_metadata is None
    assert agent.injected_tool_messages[0]["tool_call_id"] == "tc1"


@pytest.mark.asyncio
async def test_legacy_single_approval_retries_safe_timeout_with_attempt_metadata():
    uow = _Uow()
    tool_pack = _LegacyRetryTool()
    agent = _ResumeAgent(
        uow_factory=lambda: uow,
        session_id="s1",
        agent_config=SimpleNamespace(
            max_retries=2,
            max_iterations=1,
            tool_result_max_chars=8000,
        ),
        llm=_LLM(),
        json_parser=_Parser(),
        tools=[tool_pack],
        observability_port=agent_test_observability_port(),
        runtime_settings=agent_test_runtime_settings(),
    )
    session = uow.session.session
    session.pending_metadata = {
        "pending_tool_call": {
            "tool_call_id": "legacy-safe",
            "tool_name": "legacy_safe_timeout",
            "args": {},
        }
    }

    events = [
        event
        async for event in agent._resume_single_tool_approval(
            session,
            Message(message="approve"),
            Step(description="legacy safe retry"),
        )
    ]

    invoked = next(event for event in events if isinstance(event, ToolEvent))
    assert tool_pack.safe_timeout_calls == 2
    assert invoked.function_result.status is ToolExecutionStatus.SUCCESS
    assert [attempt.attempt_number for attempt in invoked.function_result.attempts] == [1, 2]


@pytest.mark.asyncio
async def test_legacy_single_approval_claims_keyed_write_once_then_reuses_key():
    uow = _Uow()
    tool_pack = _LegacyRetryTool()
    agent = _ResumeAgent(
        uow_factory=lambda: uow,
        session_id="s1",
        agent_config=SimpleNamespace(
            max_retries=2,
            max_iterations=1,
            tool_result_max_chars=8000,
        ),
        llm=_LLM(),
        json_parser=_Parser(),
        tools=[tool_pack],
        observability_port=agent_test_observability_port(),
        runtime_settings=agent_test_runtime_settings(),
    )
    session = uow.session.session
    session.pending_metadata = {
        "pending_tool_call": {
            "tool_call_id": "legacy-keyed",
            "tool_name": "legacy_keyed_write",
            "args": {},
        }
    }

    events = [
        event
        async for event in agent._resume_single_tool_approval(
            session,
            Message(message="approve"),
            Step(description="legacy keyed retry"),
        )
    ]

    invoked = next(event for event in events if isinstance(event, ToolEvent))
    assert tool_pack.keyed_calls == 2
    assert len(set(tool_pack.received_keys)) == 1
    assert invoked.function_result.status is ToolExecutionStatus.SUCCESS
    assert uow.resource_governance.batch.status is ApprovalStatus.CONSUMED


@pytest.mark.asyncio
async def test_single_call_gate_compatibility_uses_batch_projection_only():
    uow = _Uow()
    effect_tool = _Tool()
    agent = _Agent(
        uow_factory=lambda: uow,
        session_id="s1",
        agent_config=SimpleNamespace(
            max_retries=1,
            max_iterations=1,
            tool_result_max_chars=8000,
        ),
        llm=_LLM(),
        json_parser=_Parser(),
        tools=[effect_tool],
        observability_port=agent_test_observability_port(),
        runtime_settings=agent_test_runtime_settings(),
    )
    events = []

    gated = await agent._enter_tool_approval_gate(
        function_name="browser_click",
        function_args={"target": "submit"},
        tool_call_id="tc1",
        events=events,
    )

    assert gated is True
    assert uow.resource_governance.batch.calls[0].tool_call_id == "tc1"
    assert uow.session.session.pending_metadata[
        "approval_batch_id"
    ] == uow.resource_governance.batch.id
    assert "pending_tool_call" not in uow.session.session.pending_metadata


@pytest.mark.asyncio
async def test_react_does_not_override_partial_route_decisions():
    uow = _Uow()
    effect_tool = _Tool()
    agent = _ResumeAgent(
        uow_factory=lambda: uow,
        session_id="s1",
        agent_config=SimpleNamespace(
            max_retries=1,
            max_iterations=1,
            tool_result_max_chars=8000,
        ),
        llm=_LLM(),
        json_parser=_Parser(),
        tools=[effect_tool],
        observability_port=agent_test_observability_port(),
        runtime_settings=agent_test_runtime_settings(),
    )
    batch = await agent._get_tool_batch_executor().preflight([
        _tool_call("tc1", "browser_click", {"target": "one"}),
        _tool_call("tc2", "browser_click", {"target": "two"}),
    ])
    await uow.resource_governance.decide_approval_call(
        "tc1",
        ApprovalStatus.APPROVED,
        "u1",
    )
    session = uow.session.session
    session.owner_user_id = "u1"
    session.pending_phase = "tool_approval"
    session.pending_metadata = {"approval_batch_id": batch.batch_id}

    events = [
        event
        async for event in agent._resume_tool_approval(
            session,
            Message(message="approve"),
            Step(description="partial approval"),
        )
    ]

    assert effect_tool.invocations == []
    assert isinstance(events[-1], WaitEvent)
    assert uow.resource_governance.batch.calls[1].status == ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_execute_step_keeps_partial_approval_waiting_and_not_completed():
    uow = _Uow()
    effect_tool = _Tool()
    agent = _ResumeAgent(
        uow_factory=lambda: uow,
        session_id="s1",
        agent_config=SimpleNamespace(
            max_retries=1,
            max_iterations=1,
            tool_result_max_chars=8000,
        ),
        llm=_LLM(),
        json_parser=_Parser(),
        tools=[effect_tool],
        observability_port=agent_test_observability_port(),
        runtime_settings=agent_test_runtime_settings(),
    )
    batch = await agent._get_tool_batch_executor().preflight([
        _tool_call("tc1", "browser_click", {"target": "one"}),
        _tool_call("tc2", "browser_click", {"target": "two"}),
    ])
    await uow.resource_governance.decide_approval_call(
        "tc1",
        ApprovalStatus.APPROVED,
        "u1",
    )
    session = uow.session.session
    session.owner_user_id = "u1"
    session.pending_phase = "tool_approval"
    session.pending_metadata = {"approval_batch_id": batch.batch_id}
    step = Step(description="partial approval")
    plan = Plan(language="en", steps=[step])

    events = [
        event
        async for event in agent.execute_step(
            plan,
            step,
            Message(message="approve"),
        )
    ]

    assert isinstance(events[-1], WaitEvent)
    assert step.status == ExecutionStatus.RUNNING
    assert session.pending_phase == "tool_approval"
    assert uow.resource_governance.batch.calls[1].status == ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_react_approve_same_only_tracks_newly_approved_pending_calls():
    uow = _Uow()
    effect_tool = _Tool()
    agent = _ResumeAgent(
        uow_factory=lambda: uow,
        session_id="s1",
        agent_config=SimpleNamespace(
            max_retries=1,
            max_iterations=1,
            tool_result_max_chars=8000,
        ),
        llm=_LLM(),
        json_parser=_Parser(),
        tools=[effect_tool],
        observability_port=agent_test_observability_port(),
        runtime_settings=agent_test_runtime_settings(),
    )
    batch = await agent._get_tool_batch_executor().preflight([
        _tool_call("tc1", "write_file", {"path": "one"}),
        _tool_call("tc2", "browser_click", {"target": "submit"}),
    ])
    session = uow.session.session
    session.owner_user_id = "u1"
    session.pending_phase = "tool_approval"
    session.pending_metadata = {"approval_batch_id": batch.batch_id}

    async for _ in agent._resume_tool_approval(
        session,
        Message(message="approve_same"),
        Step(description="approve same tool"),
    ):
        pass

    assert session.pending_metadata["approved_tools"] == ["browser_click"]


@pytest.mark.parametrize(
    ("change_kind", "replacement_tool"),
    [
        ("policy", _ChangedEffectTool),
        ("capability", _ChangedCapabilityTool),
        ("signature", _ChangedSignatureTool),
        ("authorization", _Tool),
    ],
)
@pytest.mark.asyncio
async def test_react_maps_resume_revalidation_changes_to_tool_failures(
    change_kind,
    replacement_tool,
):
    uow = _Uow()
    initial_tool = _Tool()
    agent = _ResumeAgent(
        uow_factory=lambda: uow,
        session_id="s1",
        agent_config=SimpleNamespace(
            max_retries=1,
            max_iterations=1,
            tool_result_max_chars=8000,
        ),
        llm=_LLM(),
        json_parser=_Parser(),
        tools=[initial_tool],
        observability_port=agent_test_observability_port(),
        runtime_settings=agent_test_runtime_settings(),
    )
    batch = await agent._get_tool_batch_executor().preflight(
        [_tool_call("tc1", "browser_click", {"target": "submit"})]
    )
    replacement = replacement_tool()
    authorization_resolver = (
        (lambda _call: False)
        if change_kind == "authorization"
        else None
    )
    agent._batch_executor = ToolBatchExecutor(
        session_id="s1",
        tools=[replacement],
        approval_repository=uow.resource_governance,
        authorization_resolver=authorization_resolver,
    )
    session = uow.session.session
    session.owner_user_id = "u1"
    session.pending_phase = "tool_approval"
    session.pending_metadata = {"approval_batch_id": batch.batch_id}

    events = [
        event
        async for event in agent._resume_tool_approval(
            session,
            Message(message="approve"),
            Step(description="changed approval"),
        )
    ]

    assert replacement.invocations == []
    assert uow.resource_governance.batch.status == ApprovalStatus.APPROVED
    assert agent.injected_tool_messages[0]["tool_call_id"] == "tc1"
    assert any(isinstance(event, MessageEvent) for event in events)
