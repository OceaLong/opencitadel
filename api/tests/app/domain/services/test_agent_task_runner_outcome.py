#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.external.task import (
    RecoverableTaskInputUnavailable,
    RecoverableTaskReconciliationRequired,
)
from app.domain.models.event import (
    ApprovalEvent,
    AssistantNoticeEvent,
    DoneEvent,
    ErrorEvent,
    MessageEvent,
    SessionStatusEvent,
    TitleEvent,
    WaitEvent,
)
from app.domain.models.run_outcome import RunOutcome
from app.domain.models.session import SessionStatus
from app.domain.services.agent.event_emitter import AgentEventEmitter
from app.domain.services.agent_task_runner import AgentTaskRunner
from app.domain.services.flows.base import FlowStatus
from app.domain.services.flows.code_ask_flow import CodeAskFlow
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.external.task.task_state import TaskStateService, TaskStatus


class _InputStream:
    async def size(self) -> int:
        return 0


class _QueuedInputStream:
    def __init__(self, events):
        self._events = list(events)
        self._next_id = 1

    async def pop(self):
        if not self._events:
            return None, None
        event = self._events.pop(0)
        if isinstance(event, tuple):
            return event
        return event.id, event.model_dump_json()

    async def size(self):
        return len(self._events)

    async def put(self, message):
        message_id = f"queued-{self._next_id}"
        self._next_id += 1
        self._events.append((message_id, message))
        return message_id

    async def delete_message(self, message_id):
        for index, item in enumerate(self._events):
            if isinstance(item, tuple) and item[0] == message_id:
                self._events.pop(index)
                return True
        return False

    async def get_range(self, _start="-", _end="+", count=100):
        for item in self._events[:count]:
            if isinstance(item, tuple):
                yield item
            else:
                yield item.id, item.model_dump_json()


def _runner_for_outcome(status: str, *, error=None, cancelled: bool = False):
    runner = object.__new__(AgentTaskRunner)
    runner._session_id = "session-1"
    runner._run_epoch_id = None
    runner._unpersisted_run_reconciliation = None
    runner._unpersisted_reconciliation_message_id = None
    runner._agent_config = SimpleNamespace(max_run_seconds=60)
    runner._sandbox_lifecycle = SimpleNamespace(
        ensure_ready=AsyncMock(),
        create_user_message_checkpoint=AsyncMock(),
    )
    runner._initialize_integration_tool = AsyncMock()
    runner._mcp_tool = MagicMock()
    runner._mcp_config = MagicMock()
    runner._a2a_tool = MagicMock()
    runner._a2a_config = MagicMock()
    runner._sandbox_provider = SimpleNamespace(materialized=lambda: None)
    runner._attachment_sync = MagicMock()
    runner._build_vision_attachments = AsyncMock(return_value=[])
    runner._checkpoint_service = None
    runner._codebase_id = None
    runner._flush_event_persist_buffer = AsyncMock()
    runner._cleanup_tools = AsyncMock()
    runner._put_and_add_event = AsyncMock()
    runner._observability = MagicMock()
    runner._terminal_session_status = None
    runner._task_state_port = _DurableTaskState()
    flow_outcome = {
        "succeeded": RunOutcome.succeeded(),
        "waiting": RunOutcome.waiting(),
        "cancelled": RunOutcome.cancelled(),
        "failed": RunOutcome.failed(
            error.message if error else "flow failed",
            code=error.code if error else "FLOW_FAILED",
        ),
    }[status]
    runner._flow = SimpleNamespace(outcome=flow_outcome)
    runner._pop_event = AsyncMock(
        side_effect=[MessageEvent(role="user", message="hello"), None],
    )
    runner._is_cancelled = AsyncMock(return_value=cancelled)
    statuses: list[SessionStatus] = []

    async def _emit_status(_task, session_status, **_kwargs):
        statuses.append(session_status)
        if session_status != SessionStatus.RUNNING:
            runner._terminal_session_status = session_status

    runner._emit_session_status = _emit_status

    async def _run_flow(_message):
        if status == "failed":
            yield ErrorEvent(error="flow failed")
        elif False:
            yield

    runner._run_flow = _run_flow
    task = SimpleNamespace(id="task-1", input_stream=_InputStream())
    return runner, task, statuses


@pytest.mark.asyncio
async def test_error_outcome_never_emits_completed():
    runner, task, statuses = _runner_for_outcome(
        "failed",
        error=SimpleNamespace(message="flow failed", code="FLOW_FAILED"),
    )

    outcome = await runner.invoke(task)

    assert statuses == [SessionStatus.RUNNING, SessionStatus.FAILED]
    assert getattr(outcome, "status", None) == "failed"


@pytest.mark.asyncio
async def test_waiting_outcome_is_not_completed():
    runner, task, statuses = _runner_for_outcome("waiting")

    outcome = await runner.invoke(task)

    assert statuses == [SessionStatus.RUNNING, SessionStatus.WAITING]
    assert getattr(outcome, "status", None) == "waiting"
    assert runner.terminal_status == SessionStatus.WAITING


@pytest.mark.asyncio
async def test_normal_outcome_is_completed():
    runner, task, statuses = _runner_for_outcome("succeeded")

    outcome = await runner.invoke(task)

    assert statuses == [SessionStatus.RUNNING, SessionStatus.COMPLETED]
    assert getattr(outcome, "status", None) == "succeeded"


@pytest.mark.asyncio
async def test_cancelled_run_has_cancelled_outcome():
    runner, task, statuses = _runner_for_outcome("succeeded", cancelled=True)

    outcome = await runner.invoke(task)

    assert statuses == [SessionStatus.RUNNING, SessionStatus.CANCELLED]
    assert getattr(outcome, "status", None) == "cancelled"


class _OutputStream:
    def __init__(self) -> None:
        self.events = []

    async def put(self, event_json: str) -> str:
        self.events.append(event_json)
        return f"stream-{len(self.events)}"


class _FailOnceOutputStream(_OutputStream):
    async def put(self, event_json: str) -> str:
        if not self.events:
            self.events.append("failed-attempt")
            raise RuntimeError("stream unavailable")
        return await super().put(event_json)


class _EmitterUow:
    def __init__(
        self,
        persisted,
        replay_events=None,
        session_state=None,
    ) -> None:
        self._persisted = persisted
        self._replay_events = replay_events or []
        self._session_state = session_state
        self.session = self

    async def list_events(
            self,
            *_args,
            before=None,
            limit=100,
            latest=False,
            **_kwargs,
    ):
        persisted_events = [
            (int(payload["id"]), event)
            for event, payload in self._persisted
        ]
        events = sorted(
            [*self._replay_events, *persisted_events],
            key=lambda record: record[0],
        )
        if before is not None:
            events = [record for record in events if record[0] < before]
        if latest or before is not None:
            return events[-limit:]
        return events[:limit]

    async def add_event_payloads(self, _session_id, payloads):
        self._persisted.extend(payloads)

    async def claim_session_status_event(self, _session_id, event, payload):
        status_events = [
            persisted_event
            for persisted_event, _persisted_payload in self._persisted
            if isinstance(persisted_event, SessionStatusEvent)
        ]
        status_events = [
            replay_event
            for _seq, replay_event in self._replay_events
            if isinstance(replay_event, SessionStatusEvent)
        ] + status_events
        latest_running = next(
            (
                persisted_event
                for persisted_event in reversed(status_events)
                if persisted_event.status == "running"
            ),
            None,
        )
        latest_terminal = next(
            (
                persisted_event
                for persisted_event in reversed(status_events)
                if persisted_event.status
                in {"waiting", "completed", "cancelled", "failed"}
            ),
            None,
        )
        if event.status == "running":
            if (
                latest_running is not None
                and latest_running.run_epoch_id == event.run_epoch_id
            ):
                return False
        elif (
            latest_running is None
            or latest_running.run_epoch_id != event.run_epoch_id
            or (
                latest_terminal is not None
                and status_events.index(latest_terminal)
                > status_events.index(latest_running)
            )
        ):
            return False
        self._persisted.append((event, dict(payload)))
        if self._session_state is not None:
            self._session_state["status"] = event.status
            self._session_state["run_epoch_id"] = event.run_epoch_id
        return True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _event_emitter(*, replay_events=None):
    persisted = []
    output_stream = _OutputStream()
    sequence = SimpleNamespace(allocate=AsyncMock(side_effect=range(1, 100)))
    task_state = SimpleNamespace(set_output_seq_cursor=AsyncMock())
    emitter = AgentEventEmitter(
        session_id="session-1",
        uow_factory=lambda: _EmitterUow(persisted, replay_events),
        event_sequence=sequence,
        task_state_port=task_state,
    )
    task = SimpleNamespace(id="task-1", output_stream=output_stream)
    return emitter, task, persisted, output_stream


@pytest.mark.asyncio
async def test_concurrent_terminal_emits_persist_only_first_status():
    emitter, task, persisted, output_stream = _event_emitter()
    await emitter.emit(
        task,
        SessionStatusEvent(status="running", run_epoch_id="epoch-1"),
    )

    await asyncio.gather(
        emitter.emit(
            task,
            SessionStatusEvent(status="failed", run_epoch_id="epoch-1"),
        ),
        emitter.emit(
            task,
            SessionStatusEvent(status="completed", run_epoch_id="epoch-1"),
        ),
    )
    await emitter.flush()

    statuses = [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ]
    assert statuses == ["running", "failed"]
    assert len(output_stream.events) == 2


@pytest.mark.asyncio
async def test_replayed_terminal_rejects_late_terminal_for_same_run():
    replay_events = [
        (1, SessionStatusEvent(status="running", run_epoch_id="epoch-1")),
        (2, SessionStatusEvent(status="failed", run_epoch_id="epoch-1")),
    ]
    emitter, task, persisted, output_stream = _event_emitter(replay_events=replay_events)

    accepted = await emitter.emit(
        task,
        SessionStatusEvent(status="completed", run_epoch_id="epoch-1"),
    )
    await emitter.flush()

    assert accepted is False
    assert persisted == []
    assert output_stream.events == []


@pytest.mark.asyncio
async def test_replay_guard_pages_back_to_find_terminal_epoch_boundary():
    replay_events = [
        (1, SessionStatusEvent(status="running", run_epoch_id="epoch-1")),
        (2, SessionStatusEvent(status="failed", run_epoch_id="epoch-1")),
        *[
            (seq, MessageEvent(role="assistant", message=f"event-{seq}"))
            for seq in range(3, 203)
        ],
    ]
    emitter, task, persisted, output_stream = _event_emitter(replay_events=replay_events)

    accepted = await emitter.emit(
        task,
        SessionStatusEvent(status="completed", run_epoch_id="epoch-1"),
    )
    await emitter.flush()

    assert accepted is False
    assert persisted == []
    assert output_stream.events == []


@pytest.mark.asyncio
async def test_new_running_event_starts_new_terminal_guard_epoch():
    replay_events = [
        (1, SessionStatusEvent(status="running")),
        (2, SessionStatusEvent(status="failed")),
    ]
    emitter, task, persisted, _output_stream = _event_emitter(replay_events=replay_events)

    await emitter.emit(task, SessionStatusEvent(status="running"))
    accepted = await emitter.emit(task, SessionStatusEvent(status="completed"))
    await emitter.flush()

    assert accepted is True
    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running", "completed"]


@pytest.mark.asyncio
async def test_waiting_rejects_completed_until_a_new_running_epoch():
    emitter, task, persisted, _output_stream = _event_emitter()

    await emitter.emit(task, SessionStatusEvent(status="running"))
    assert await emitter.emit(task, SessionStatusEvent(status="waiting")) is True
    assert await emitter.emit(task, SessionStatusEvent(status="completed")) is False
    await emitter.flush()

    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running", "waiting"]


@pytest.mark.asyncio
async def test_new_running_epoch_can_complete_after_waiting():
    emitter, task, persisted, _output_stream = _event_emitter()

    await emitter.emit(task, SessionStatusEvent(status="running"))
    await emitter.emit(task, SessionStatusEvent(status="waiting"))
    await emitter.emit(task, SessionStatusEvent(status="running"))
    assert await emitter.emit(task, SessionStatusEvent(status="completed")) is True
    await emitter.flush()

    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running", "waiting", "running", "completed"]


@pytest.mark.asyncio
async def test_terminal_claim_is_retryable_when_emission_never_reaches_persistence_buffer():
    emitter, task, persisted, _output_stream = _event_emitter()
    await emitter.emit(
        task,
        SessionStatusEvent(status="running", run_epoch_id="epoch-1"),
    )
    task.output_stream = _FailOnceOutputStream()

    accepted = await emitter.emit(
        task,
        SessionStatusEvent(status="failed", run_epoch_id="epoch-1"),
    )
    await emitter.flush()

    assert accepted is True
    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running", "failed"]


@pytest.mark.asyncio
async def test_redis_task_keeps_waiting_outcome_pending():
    task = RedisStreamTask.__new__(RedisStreamTask)
    task._id = "task-1"
    task._task_runner = SimpleNamespace(
        invoke=AsyncMock(return_value=SimpleNamespace(status="waiting")),
        on_done=AsyncMock(),
    )
    task._task_state = SimpleNamespace(
        get_status=AsyncMock(return_value=TaskStatus.RUNNING),
        set_status=AsyncMock(),
    )
    RedisStreamTask._local_executions[task._id] = MagicMock()

    await task._execute_task()

    task._task_state.set_status.assert_awaited_with(
        task.id,
        1,
        TaskStatus.PENDING,
    )
    assert all(
        call.args[2] != TaskStatus.DONE
        for call in task._task_state.set_status.await_args_list
    )


@pytest.mark.asyncio
async def test_missing_input_does_not_open_a_running_epoch():
    runner, task, _statuses = _runner_for_outcome("succeeded")
    persisted = []
    runner._event_emitter = AgentEventEmitter(
        session_id="session-1",
        uow_factory=lambda: _EmitterUow(persisted),
        event_sequence=SimpleNamespace(
            allocate=AsyncMock(side_effect=range(1, 100))
        ),
        task_state_port=SimpleNamespace(
            set_output_seq_cursor=AsyncMock()
        ),
    )
    runner._pop_event = AsyncMock(return_value=None)
    runner._session_state = SimpleNamespace(
        transition=AsyncMock(
            return_value=SessionStatusEvent(status="running")
        )
    )
    task.output_stream = _OutputStream()
    del runner._emit_session_status
    del runner._put_and_add_event
    del runner._flush_event_persist_buffer

    with pytest.raises(RecoverableTaskInputUnavailable):
        await runner.invoke(task)

    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == []


class _ApprovalWaitingAgent:
    def set_locale(self, _locale):
        return None

    async def invoke(self, *_args, **_kwargs):
        yield ApprovalEvent(
            approval_id="approval-1",
            kind="tool",
            payload={"tool_call_ids": ["tool-1"]},
        )
        yield WaitEvent(reason="tool_approval")


@pytest.mark.asyncio
async def test_ask_approval_wait_maps_real_runner_and_redis_task_to_pending():
    runner, _unused_task, _statuses = _runner_for_outcome("succeeded")
    persisted = []
    runner._event_emitter = AgentEventEmitter(
        session_id="session-1",
        uow_factory=lambda: _EmitterUow(persisted),
        event_sequence=SimpleNamespace(
            allocate=AsyncMock(side_effect=range(1, 100))
        ),
        task_state_port=SimpleNamespace(
            set_output_seq_cursor=AsyncMock()
        ),
    )
    flow = CodeAskFlow.__new__(CodeAskFlow)
    flow.status = FlowStatus.EXECUTING
    flow._agent = _ApprovalWaitingAgent()
    runner._flow = flow
    runner._on_complete_callback = None
    runner._on_session_terminal_callback = None
    for shadowed_method in (
        "_emit_session_status",
        "_put_and_add_event",
        "_flush_event_persist_buffer",
        "_run_flow",
        "_pop_event",
    ):
        delattr(runner, shadowed_method)

    task = RedisStreamTask.__new__(RedisStreamTask)
    task._id = "task-ask-approval"
    task._session_id = "session-1"
    task._task_runner = runner
    task._input_stream = _QueuedInputStream(
        [MessageEvent(role="user", message="use the governed tool")]
    )
    task._output_stream = _OutputStream()
    task._task_state = SimpleNamespace(
        get_status=AsyncMock(return_value=TaskStatus.RUNNING),
        set_status=AsyncMock(),
    )
    RedisStreamTask._local_executions[task.id] = MagicMock()

    await task._execute_task()

    task._task_state.set_status.assert_awaited_with(
        task.id,
        1,
        TaskStatus.PENDING,
    )
    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running", "waiting"]
    assert not any(
        isinstance(event, DoneEvent)
        for event, _payload in persisted
    )


class _CancellingFlow:
    async def invoke(self, _message):
        if False:
            yield
        raise asyncio.CancelledError


class _CancellationOutputStream(_OutputStream):
    async def put(self, event_json):
        self.events.append(event_json)
        payload = json.loads(event_json)
        if (
            payload.get("type") == "session_status"
            and payload.get("status") == "cancelled"
        ):
            raise RuntimeError("cancel stream failed after publication")
        return f"stream-{len(self.events)}"


class _FailCancelledClaimOnceUow(_EmitterUow):
    def __init__(self, persisted, failure_state):
        super().__init__(persisted)
        self._failure_state = failure_state

    async def claim_session_status_event(self, session_id, event, payload):
        if (
            event.status == "cancelled"
            and self._failure_state["remaining"] > 0
        ):
            self._failure_state["remaining"] -= 1
            raise RuntimeError("cancel terminal database flush failed")
        return await super().claim_session_status_event(
            session_id,
            event,
            payload,
        )


@pytest.mark.asyncio
async def test_cancellation_survives_terminal_db_retry_and_stream_failure():
    runner, _unused_task, _statuses = _runner_for_outcome("succeeded")
    persisted = []
    failure_state = {"remaining": 1}
    runner._event_emitter = AgentEventEmitter(
        session_id="session-1",
        uow_factory=lambda: _FailCancelledClaimOnceUow(
            persisted,
            failure_state,
        ),
        event_sequence=SimpleNamespace(
            allocate=AsyncMock(side_effect=range(1, 100))
        ),
        task_state_port=SimpleNamespace(
            set_output_seq_cursor=AsyncMock()
        ),
    )
    runner._flow = _CancellingFlow()
    runner._on_complete_callback = None
    runner._on_session_terminal_callback = None
    for shadowed_method in (
        "_emit_session_status",
        "_put_and_add_event",
        "_flush_event_persist_buffer",
        "_run_flow",
        "_pop_event",
    ):
        delattr(runner, shadowed_method)
    task = SimpleNamespace(
        id="task-cancel",
        input_stream=_QueuedInputStream(
            [MessageEvent(role="user", message="cancel me")]
        ),
        output_stream=_CancellationOutputStream(),
    )

    with pytest.raises(asyncio.CancelledError):
        await runner.invoke(task)

    assert runner.terminal_status == SessionStatus.CANCELLED
    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running", "cancelled"]


class _ApprovalThenSuccessAgent:
    def __init__(self):
        self.invocations = 0

    def set_locale(self, _locale):
        return None

    async def invoke(self, *_args, **_kwargs):
        self.invocations += 1
        if self.invocations == 1:
            yield ApprovalEvent(
                approval_id="approval-queued",
                kind="tool",
                payload={"tool_call_ids": ["tool-queued"]},
            )
            yield WaitEvent(reason="tool_approval")


@pytest.mark.asyncio
async def test_queued_approval_response_runs_in_a_new_epoch():
    runner, _unused_task, _statuses = _runner_for_outcome("succeeded")
    persisted = []
    runner._event_emitter = AgentEventEmitter(
        session_id="session-1",
        uow_factory=lambda: _EmitterUow(persisted),
        event_sequence=SimpleNamespace(
            allocate=AsyncMock(side_effect=range(1, 200))
        ),
        task_state_port=SimpleNamespace(
            set_output_seq_cursor=AsyncMock()
        ),
    )
    agent = _ApprovalThenSuccessAgent()
    flow = CodeAskFlow.__new__(CodeAskFlow)
    flow.status = FlowStatus.EXECUTING
    flow._agent = agent
    runner._flow = flow
    runner._on_complete_callback = None
    runner._on_session_terminal_callback = None
    for shadowed_method in (
        "_emit_session_status",
        "_put_and_add_event",
        "_flush_event_persist_buffer",
        "_run_flow",
        "_pop_event",
    ):
        delattr(runner, shadowed_method)

    first = MessageEvent(
        id="input-question",
        role="user",
        message="use the governed tool",
    )
    approval = MessageEvent(
        id="input-approval",
        role="user",
        message="approved",
    )
    task = RedisStreamTask.__new__(RedisStreamTask)
    task._id = "task-queued-approval"
    task._session_id = "session-1"
    task._task_runner = runner
    task._input_stream = _QueuedInputStream([first, approval])
    task._output_stream = _OutputStream()
    task._task_state = SimpleNamespace(
        get_status=AsyncMock(return_value=TaskStatus.RUNNING),
        set_status=AsyncMock(),
        clear_run_reconciliation=(
            runner._task_state_port.clear_run_reconciliation
        ),
    )

    RedisStreamTask._local_executions[task.id] = MagicMock()
    await task._execute_task()

    assert task._task_state.set_status.await_args_list[-1].args[2] == (
        TaskStatus.PENDING
    )
    assert await task.input_stream.size() == 1
    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running", "waiting"]

    RedisStreamTask._local_executions[task.id] = MagicMock()
    await task._execute_task()

    assert task._task_state.set_status.await_args_list[-1].args[2] == (
        TaskStatus.DONE
    )
    assert [
        (payload["status"], payload["run_epoch_id"])
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == [
        ("running", "task-queued-approval:input-question"),
        ("waiting", "task-queued-approval:input-question"),
        ("running", "task-queued-approval:input-approval"),
        ("completed", "task-queued-approval:input-approval"),
    ]


class _AlwaysFailTerminalUow(_EmitterUow):
    async def claim_session_status_event(self, session_id, event, payload):
        if event.status in {"waiting", "completed", "cancelled", "failed"}:
            raise RuntimeError("terminal database unavailable")
        return await super().claim_session_status_event(
            session_id,
            event,
            payload,
        )


@pytest.mark.parametrize(
    ("flow_status", "cancelled"),
    [
        ("succeeded", False),
        ("failed", False),
        ("succeeded", True),
    ],
)
@pytest.mark.asyncio
async def test_persistent_terminal_claim_failure_keeps_redis_pending(
    flow_status,
    cancelled,
):
    runner, _unused_task, _statuses = _runner_for_outcome(flow_status)
    persisted = []
    runner._event_emitter = AgentEventEmitter(
        session_id="session-1",
        uow_factory=lambda: _AlwaysFailTerminalUow(persisted),
        event_sequence=SimpleNamespace(
            allocate=AsyncMock(side_effect=range(1, 200))
        ),
        task_state_port=SimpleNamespace(
            set_output_seq_cursor=AsyncMock()
        ),
    )
    runner._on_complete_callback = None
    runner._on_session_terminal_callback = None
    for shadowed_method in (
        "_emit_session_status",
        "_put_and_add_event",
        "_flush_event_persist_buffer",
    ):
        delattr(runner, shadowed_method)
    if cancelled:
        runner._flow = _CancellingFlow()
        del runner._run_flow

    task = RedisStreamTask.__new__(RedisStreamTask)
    task._id = f"task-persist-{flow_status}-{cancelled}"
    task._session_id = "session-1"
    task._task_runner = runner
    task._input_stream = _InputStream()
    task._output_stream = _OutputStream()
    task._task_state = SimpleNamespace(
        get_status=AsyncMock(return_value=TaskStatus.RUNNING),
        set_status=AsyncMock(),
    )
    RedisStreamTask._local_executions[task.id] = MagicMock()

    await task._execute_task()

    assert task._task_state.set_status.await_args_list[-1].args[2] == (
        TaskStatus.PENDING
    )
    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running"]


def _real_redis_runner_for_shared_epoch(
    status,
    persisted,
    session_state,
):
    runner, _unused_task, _statuses = _runner_for_outcome(status)
    runner._pop_event = AsyncMock(
        side_effect=[
            MessageEvent(
                id="shared-input",
                role="user",
                message="same invocation",
            ),
            None,
        ]
    )
    runner._event_emitter = AgentEventEmitter(
        session_id="session-1",
        uow_factory=lambda: _EmitterUow(
            persisted,
            session_state=session_state,
        ),
        event_sequence=SimpleNamespace(
            allocate=AsyncMock(side_effect=range(1, 200))
        ),
        task_state_port=SimpleNamespace(
            set_output_seq_cursor=AsyncMock()
        ),
    )
    runner._on_complete_callback = AsyncMock()
    runner._on_session_terminal_callback = None
    for shadowed_method in (
        "_emit_session_status",
        "_put_and_add_event",
        "_flush_event_persist_buffer",
    ):
        delattr(runner, shadowed_method)
    task = RedisStreamTask.__new__(RedisStreamTask)
    task._id = "shared-task"
    task._session_id = "session-1"
    task._task_runner = runner
    task._input_stream = _InputStream()
    task._output_stream = _OutputStream()
    task._task_state = SimpleNamespace(
        get_status=AsyncMock(return_value=TaskStatus.RUNNING),
        set_status=AsyncMock(),
    )
    return runner, task


@pytest.mark.asyncio
async def test_two_runners_map_redis_to_the_authoritative_terminal_winner():
    persisted = []
    session_state = {"status": None, "run_epoch_id": None}
    failed_runner, failed_task = _real_redis_runner_for_shared_epoch(
        "failed",
        persisted,
        session_state,
    )
    success_runner, success_task = _real_redis_runner_for_shared_epoch(
        "succeeded",
        persisted,
        session_state,
    )
    RedisStreamTask._local_executions[failed_task.id] = MagicMock()

    await asyncio.gather(
        failed_task._execute_task(),
        success_task._execute_task(),
    )

    terminal = [
        payload["status"]
        for event, payload in persisted
        if (
            isinstance(event, SessionStatusEvent)
            and payload["status"] != "running"
        )
    ]
    assert len(terminal) == 1
    authoritative = terminal[0]
    expected_outcome = {
        "failed": "failed",
        "completed": "succeeded",
    }[authoritative]
    expected_task_status = {
        "failed": TaskStatus.FAILED,
        "completed": TaskStatus.DONE,
    }[authoritative]
    assert failed_runner._run_outcome.status == expected_outcome
    assert success_runner._run_outcome.status == expected_outcome
    expected_session_status = SessionStatus(authoritative)
    assert failed_runner.terminal_status == expected_session_status
    assert success_runner.terminal_status == expected_session_status
    assert session_state == {
        "status": authoritative,
        "run_epoch_id": "shared-task:shared-input",
    }
    assert failed_task._task_state.set_status.await_args_list[-1].args[2] == (
        expected_task_status
    )
    assert success_task._task_state.set_status.await_args_list[-1].args[2] == (
        expected_task_status
    )
    for runner in (failed_runner, success_runner):
        if authoritative == "completed":
            runner._on_complete_callback.assert_awaited_once_with(
                "session-1"
            )
        else:
            runner._on_complete_callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_accepted_success_returns_original_usage_and_redacts_internal_outcome():
    persisted = []
    session_state = {"status": None, "run_epoch_id": None}
    runner, task = _real_redis_runner_for_shared_epoch(
        "succeeded",
        persisted,
        session_state,
    )
    proposed = RunOutcome.succeeded(
        usage={"tokens": 7, "estimated_cost_usd": 0.25},
    )
    runner._flow = SimpleNamespace(outcome=proposed)

    returned = await runner.invoke(task)

    terminal_event, terminal_payload = next(
        (event, payload)
        for event, payload in persisted
        if (
            isinstance(event, SessionStatusEvent)
            and event.status == "completed"
        )
    )
    assert returned == proposed
    assert terminal_event.outcome == proposed
    assert terminal_payload["outcome"] == proposed.model_dump(mode="json")
    published_terminal = next(
        json.loads(payload)
        for payload in task.output_stream.events
        if json.loads(payload).get("status") == "completed"
    )
    assert "outcome" not in published_terminal


@pytest.mark.parametrize(
    ("runner_status", "terminal_status", "outcome_payload"),
    [
        (
            "failed",
            "failed",
            {
                "status": "failed",
                "error": {
                    "message": "accepted failure",
                    "code": "ACCEPTED_FAILED",
                    "details": {"retryable": False},
                },
                "usage": {"tokens": 5},
            },
        ),
        (
            "cancelled",
            "cancelled",
            {
                "status": "cancelled",
                "error": {
                    "message": "cancelled by operator",
                    "code": "OPERATOR_CANCELLED",
                    "details": {"actor": "operator-1"},
                },
                "usage": {"tokens": 2},
            },
        ),
        (
            "waiting",
            "waiting",
            {
                "status": "waiting",
                "error": None,
                "usage": {"tokens": 1},
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_accepted_non_success_returns_original_payload(
    runner_status,
    terminal_status,
    outcome_payload,
):
    persisted = []
    runner, task = _real_redis_runner_for_shared_epoch(
        runner_status,
        persisted,
        {"status": None, "run_epoch_id": None},
    )
    proposed = RunOutcome.model_validate(outcome_payload)
    runner._flow = SimpleNamespace(outcome=proposed)

    returned = await runner.invoke(task)

    terminal_payload = next(
        payload
        for event, payload in persisted
        if (
            isinstance(event, SessionStatusEvent)
            and event.status == terminal_status
        )
    )
    assert returned.model_dump(mode="json") == outcome_payload
    assert terminal_payload["outcome"] == outcome_payload


@pytest.mark.asyncio
async def test_deterministic_failed_cas_loser_adopts_full_winner_outcome():
    winner_payload = {
        "status": "failed",
        "error": {
            "message": "winner failed",
            "code": "WINNER_FAILED",
            "details": {
                "provider": "winner",
                "retryable": False,
            },
        },
        "usage": {"tokens": 11, "estimated_cost_usd": 0.4},
    }
    loser_payload = {
        "status": "failed",
        "error": {
            "message": "loser failed",
            "code": "LOSER_FAILED",
            "details": {
                "provider": "loser",
                "retryable": True,
            },
        },
        "usage": {"tokens": 3, "estimated_cost_usd": 0.1},
    }
    winner = RunOutcome.model_validate(winner_payload)
    loser = RunOutcome.model_validate(loser_payload)
    running = SessionStatusEvent(
        id="1",
        status="running",
        run_epoch_id="shared-task:shared-input",
    )
    terminal = SessionStatusEvent(
        id="2",
        status="failed",
        run_epoch_id="shared-task:shared-input",
        reason="winner failed",
        code="WINNER_FAILED",
        outcome=winner,
    )
    persisted = [
        (running, running.model_dump(mode="json")),
        (
            terminal,
            {
                **terminal.model_dump(mode="json"),
                "outcome": winner_payload,
            },
        ),
    ]
    runner, task = _real_redis_runner_for_shared_epoch(
        "failed",
        persisted,
        {
            "status": "failed",
            "run_epoch_id": "shared-task:shared-input",
        },
    )
    runner._flow = SimpleNamespace(outcome=loser)

    returned = await runner.invoke(task)

    assert returned.model_dump(mode="json") == winner_payload
    assert runner._run_outcome.model_dump(mode="json") == winner_payload
    assert runner.terminal_status == SessionStatus.FAILED


@pytest.mark.asyncio
async def test_concurrent_failed_cas_loser_adopts_full_winner_outcome():
    first_payload = {
        "status": "failed",
        "error": {
            "message": "first failed",
            "code": "FIRST_FAILED",
            "details": {"attempt": 1, "source": "first"},
        },
        "usage": {"tokens": 13, "estimated_cost_usd": 0.5},
    }
    second_payload = {
        "status": "failed",
        "error": {
            "message": "second failed",
            "code": "SECOND_FAILED",
            "details": {"attempt": 2, "source": "second"},
        },
        "usage": {"tokens": 17, "estimated_cost_usd": 0.75},
    }
    persisted = []
    session_state = {"status": None, "run_epoch_id": None}
    first_runner, first_task = _real_redis_runner_for_shared_epoch(
        "failed",
        persisted,
        session_state,
    )
    second_runner, second_task = _real_redis_runner_for_shared_epoch(
        "failed",
        persisted,
        session_state,
    )
    first_runner._flow = SimpleNamespace(
        outcome=RunOutcome.model_validate(first_payload),
    )
    second_runner._flow = SimpleNamespace(
        outcome=RunOutcome.model_validate(second_payload),
    )

    returned = await asyncio.gather(
        first_runner.invoke(first_task),
        second_runner.invoke(second_task),
    )

    terminal_payload = next(
        payload
        for event, payload in persisted
        if (
            isinstance(event, SessionStatusEvent)
            and event.status == "failed"
        )
    )
    authoritative_payload = terminal_payload["outcome"]
    assert authoritative_payload in (first_payload, second_payload)
    assert [
        outcome.model_dump(mode="json")
        for outcome in returned
    ] == [authoritative_payload, authoritative_payload]
    assert [
        runner._run_outcome.model_dump(mode="json")
        for runner in (first_runner, second_runner)
    ] == [authoritative_payload, authoritative_payload]


class _DurableTaskState:
    def __init__(self):
        self.status = TaskStatus.RUNNING
        self.reconciliation = None

    async def set_status(self, _task_id, run_generation, status):
        assert run_generation == 1
        self.status = status
        return True

    async def get_status(self, _task_id):
        return self.status

    async def is_cancelled(self, _task_id):
        return False

    async def set_output_seq_cursor(self, *_args):
        return None

    async def set_run_reconciliation(
        self,
        _task_id,
        run_generation,
        run_epoch_id,
        outcome,
    ):
        assert run_generation == 1
        self.reconciliation = {
            "run_generation": run_generation,
            "run_epoch_id": run_epoch_id,
            "outcome": dict(outcome),
        }
        return True

    async def get_run_reconciliation(self, _task_id, run_generation):
        assert run_generation == 1
        return self.reconciliation

    async def clear_run_reconciliation(self, _task_id, run_generation):
        assert run_generation == 1
        self.reconciliation = None
        return True


class _RacingRedisClient:
    """Synchronize proposal/heartbeat mutations at the production Redis boundary."""

    def __init__(self, task_id):
        self.values = {}
        self.hashes = {}
        self.expiries = {}
        self._meta_key = TaskStateService.meta_key(task_id)
        self._read_barrier = asyncio.Barrier(2)
        self._eval_barrier = asyncio.Barrier(2)
        self._barrier_gets_remaining = 2
        self._barrier_evals_remaining = 2
        self._proposal_written = asyncio.Event()
        self._atomic_lock = asyncio.Lock()
        self.race_started = asyncio.Event()

    async def get(self, key):
        value = self.values.get(key)
        if (
            key == self._meta_key
            and self.race_started.is_set()
            and self._barrier_gets_remaining > 0
        ):
            self._barrier_gets_remaining -= 1
            await self._read_barrier.wait()
        return value

    async def set(self, key, value, **kwargs):
        if key == self._meta_key:
            payload = json.loads(value)
            if "run_reconciliation" in payload:
                self.values[key] = value
                self._proposal_written.set()
            elif payload.get("worker_id") == "worker-race":
                await self._proposal_written.wait()
                self.values[key] = value
            else:
                self.values[key] = value
        else:
            self.values[key] = value
        if "ex" in kwargs:
            self.expiries[key] = kwargs["ex"]
        return True

    async def eval(
        self,
        _script,
        _num_keys,
        key,
        run_generation,
        updates,
        removals,
        ttl,
    ):
        if (
            key == self._meta_key
            and self.race_started.is_set()
            and self._barrier_evals_remaining > 0
        ):
            self._barrier_evals_remaining -= 1
            await self._eval_barrier.wait()
            self._barrier_gets_remaining = 0
        async with self._atomic_lock:
            raw = self.values.get(key)
            if raw is None:
                return -1
            payload = json.loads(raw)
            if int(payload.get("run_generation", 1)) != int(run_generation):
                return 0
            payload.update(json.loads(updates))
            for field in json.loads(removals):
                payload.pop(field, None)
            self.values[key] = json.dumps(payload)
            self.expiries[key] = int(ttl)
            return 1

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    async def expire(self, key, ttl):
        self.expiries[key] = ttl


class _SimulatedProcessCrash(BaseException):
    pass


def _production_task_state(client):
    service = TaskStateService.__new__(TaskStateService)
    service._redis = SimpleNamespace(client=client)
    return service


class _FailingMutationRedisClient(_RacingRedisClient):
    def __init__(
        self,
        task_id,
        *,
        fail_reconciliation_persistently=False,
        reconciliation_failures=0,
        status_failures=None,
        fail_all_status_mutations=False,
        fail_clear_persistently=False,
        cancel_clear_once=False,
        cancel_status_once=None,
    ):
        super().__init__(task_id)
        self.fail_reconciliation_persistently = (
            fail_reconciliation_persistently
        )
        self.reconciliation_failures = reconciliation_failures
        self.status_failures = dict(status_failures or {})
        self.fail_all_status_mutations = fail_all_status_mutations
        self.fail_clear_persistently = fail_clear_persistently
        self.cancel_clear_once = cancel_clear_once
        self.cancel_status_once = cancel_status_once
        self.status_attempts = []
        self.crash_reconciliation_once = False

    async def eval(
        self,
        script,
        num_keys,
        key,
        run_generation,
        updates,
        removals,
        ttl,
    ):
        update_data = json.loads(updates)
        removal_fields = json.loads(removals)
        if (
            self.crash_reconciliation_once
            and "run_reconciliation" in update_data
        ):
            self.crash_reconciliation_once = False
            raise _SimulatedProcessCrash
        if (
            (
                self.fail_reconciliation_persistently
                or self.reconciliation_failures > 0
            )
            and "run_reconciliation" in update_data
        ):
            if self.reconciliation_failures > 0:
                self.reconciliation_failures -= 1
            raise RuntimeError("run reconciliation mutation unavailable")
        status = update_data.get("status")
        if status is not None:
            self.status_attempts.append(status)
            if self.cancel_status_once == status:
                self.cancel_status_once = None
                raise asyncio.CancelledError
            if self.fail_all_status_mutations:
                raise RuntimeError("task status mutation unavailable")
            remaining = self.status_failures.get(status, 0)
            if remaining > 0:
                self.status_failures[status] = remaining - 1
                raise RuntimeError(
                    f"task status {status} mutation unavailable"
                )
        if (
            (
                self.fail_clear_persistently
                or self.cancel_clear_once
            )
            and "run_reconciliation" in removal_fields
        ):
            if self.cancel_clear_once:
                self.cancel_clear_once = False
                raise asyncio.CancelledError
            raise RuntimeError("run reconciliation cleanup unavailable")
        return await super().eval(
            script,
            num_keys,
            key,
            run_generation,
            updates,
            removals,
            ttl,
        )


class _RecoveringTerminalUow(_EmitterUow):
    def __init__(self, persisted, database_state):
        super().__init__(
            persisted,
            session_state=database_state,
        )
        self._database_state = database_state

    async def claim_session_status_event(self, session_id, event, payload):
        if (
            event.status in {"waiting", "completed", "cancelled", "failed"}
            and not self._database_state["available"]
        ):
            raise RuntimeError("terminal database unavailable")
        return await super().claim_session_status_event(
            session_id,
            event,
            payload,
        )


def _recoverable_runner_and_task(
    status,
    persisted,
    database_state,
    task_state,
    *,
    sequence_start,
    has_input,
):
    runner, _unused_task, _statuses = _runner_for_outcome(status)
    outcome = {
        "succeeded": RunOutcome.succeeded(),
        "failed": RunOutcome.failed(
            "agent failed",
            code="AGENT_FAILED",
        ),
    }[status]
    runner._flow = SimpleNamespace(outcome=outcome)
    runner._event_emitter = AgentEventEmitter(
        session_id="session-1",
        uow_factory=lambda: _RecoveringTerminalUow(
            persisted,
            database_state,
        ),
        event_sequence=SimpleNamespace(
            allocate=AsyncMock(
                side_effect=range(sequence_start, sequence_start + 200)
            )
        ),
        task_state_port=task_state,
    )
    runner._task_state_port = task_state
    runner._on_complete_callback = AsyncMock()
    runner._on_session_terminal_callback = None
    if has_input:
        runner._pop_event = AsyncMock(
            side_effect=[
                MessageEvent(
                    id="input-recovery",
                    role="user",
                    message="recover this run",
                ),
                None,
            ]
        )
    else:
        runner._pop_event = AsyncMock(return_value=None)
    for shadowed_method in (
        "_emit_session_status",
        "_put_and_add_event",
        "_flush_event_persist_buffer",
    ):
        delattr(runner, shadowed_method)

    task = RedisStreamTask.__new__(RedisStreamTask)
    task._id = "task-recovery"
    task._session_id = "session-1"
    task._task_runner = runner
    task._input_stream = _InputStream()
    task._output_stream = _OutputStream()
    task._task_state = task_state
    return runner, task


def _mutation_failure_runner_and_task(
    flow_status,
    persisted,
    database_state,
    task_state,
    *,
    task_id,
    sequence_start,
    has_input,
    input_stream=None,
):
    cancelled = flow_status == "cancelled"
    runner_status = "succeeded" if cancelled else flow_status
    runner, _unused_task, _statuses = _runner_for_outcome(
        runner_status,
        cancelled=cancelled,
    )
    runner._event_emitter = AgentEventEmitter(
        session_id="session-1",
        uow_factory=lambda: _RecoveringTerminalUow(
            persisted,
            database_state,
        ),
        event_sequence=SimpleNamespace(
            allocate=AsyncMock(
                side_effect=range(sequence_start, sequence_start + 200)
            )
        ),
        task_state_port=task_state,
    )
    runner._task_state_port = task_state
    runner._on_complete_callback = AsyncMock()
    runner._on_session_terminal_callback = None
    if has_input:
        runner._pop_event = AsyncMock(
            side_effect=[
                MessageEvent(
                    id="input-mutation",
                    role="user",
                    message="exercise reconciliation",
                ),
                None,
            ]
        )
    else:
        del runner._pop_event
    for shadowed_method in (
        "_emit_session_status",
        "_put_and_add_event",
        "_flush_event_persist_buffer",
    ):
        delattr(runner, shadowed_method)

    task = RedisStreamTask.__new__(RedisStreamTask)
    task._id = task_id
    task._session_id = "session-1"
    task._task_runner = runner
    task._input_stream = input_stream or _QueuedInputStream([])
    task._output_stream = _OutputStream()
    task._task_state = task_state
    return runner, task


@pytest.mark.parametrize(
    "flow_status",
    ["succeeded", "failed", "waiting", "cancelled"],
)
@pytest.mark.asyncio
async def test_persistent_proposal_mutation_failure_keeps_run_recoverable(
    flow_status,
):
    task_id = f"task-proposal-{flow_status}"
    client = _FailingMutationRedisClient(
        task_id,
        fail_reconciliation_persistently=True,
    )
    client.values[TaskStateService.meta_key(task_id)] = json.dumps(
        {
            "task_id": task_id,
            "session_id": "session-1",
            "status": TaskStatus.RUNNING.value,
        }
    )
    task_state = _production_task_state(client)
    persisted = []
    database_state = {
        "available": True,
        "status": None,
        "run_epoch_id": None,
    }
    runner, task = _mutation_failure_runner_and_task(
        flow_status,
        persisted,
        database_state,
        task_state,
        task_id=task_id,
        sequence_start=1,
        has_input=True,
    )

    await task._execute_task()

    assert runner._run_outcome.status == flow_status
    if flow_status == "failed":
        assert runner._run_outcome.error.code == "FLOW_FAILED"
    assert database_state == {
        "available": True,
        "status": "running",
        "run_epoch_id": f"{task_id}:input-mutation",
    }
    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running"]
    assert await task_state.get_status(task_id) == TaskStatus.PENDING
    assert await task_state.get_run_reconciliation(task_id) is None
    assert client.status_attempts == [TaskStatus.PENDING.value]


@pytest.mark.asyncio
async def test_proposal_write_failure_restores_intent_for_fresh_runner():
    task_id = "task-proposal-once"
    client = _FailingMutationRedisClient(
        task_id,
        reconciliation_failures=1,
    )
    client.values[TaskStateService.meta_key(task_id)] = json.dumps(
        {
            "task_id": task_id,
            "session_id": "session-1",
            "status": TaskStatus.RUNNING.value,
        }
    )
    task_state = _production_task_state(client)
    persisted = []
    database_state = {
        "available": True,
        "status": None,
        "run_epoch_id": None,
    }
    queued_input = MessageEvent(
        id="queued-next-input",
        role="user",
        message="must remain queued until reconciliation completes",
    )
    input_stream = _QueuedInputStream([queued_input])
    runner, task = _mutation_failure_runner_and_task(
        "succeeded",
        persisted,
        database_state,
        task_state,
        task_id=task_id,
        sequence_start=1,
        has_input=True,
        input_stream=input_stream,
    )

    await task._execute_task()
    assert await task_state.get_status(task_id) == TaskStatus.PENDING
    assert database_state["status"] == "running"
    assert len(input_stream._events) == 2
    _, queued_reconciliation = input_stream._events[1]
    queued_payload = json.loads(queued_reconciliation)
    assert queued_payload["type"] == "run_reconciliation"
    assert queued_payload["run_epoch_id"] == f"{task_id}:input-mutation"
    assert queued_payload["outcome"]["status"] == "succeeded"

    client.crash_reconciliation_once = True
    crashing_state = _production_task_state(client)
    _crashing_runner, crashing_task = _mutation_failure_runner_and_task(
        "failed",
        persisted,
        database_state,
        crashing_state,
        task_id=task_id,
        sequence_start=101,
        has_input=False,
        input_stream=input_stream,
    )
    with pytest.raises(_SimulatedProcessCrash):
        await crashing_task._execute_task()
    assert len(input_stream._events) == 2
    assert json.loads(input_stream._events[1][1])["type"] == (
        "run_reconciliation"
    )

    reconstructed_state = _production_task_state(client)
    fresh_runner, fresh_task = _mutation_failure_runner_and_task(
        "failed",
        persisted,
        database_state,
        reconstructed_state,
        task_id=task_id,
        sequence_start=201,
        has_input=False,
        input_stream=input_stream,
    )
    await fresh_task._execute_task()

    assert fresh_runner._run_outcome.status == "succeeded"
    assert await reconstructed_state.get_status(task_id) == TaskStatus.DONE
    assert (
        await reconstructed_state.get_run_reconciliation(task_id)
        is None
    )
    assert input_stream._events == [queued_input]
    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running", "completed"]


@pytest.mark.asyncio
async def test_malformed_reconciliation_envelope_is_recoverable_and_unconsumed():
    task_id = "task-malformed-reconciliation"
    client = _FailingMutationRedisClient(task_id)
    client.values[TaskStateService.meta_key(task_id)] = json.dumps(
        {
            "task_id": task_id,
            "session_id": "session-1",
            "status": TaskStatus.RUNNING.value,
        }
    )
    task_state = _production_task_state(client)
    ordinary = MessageEvent(
        id="ordinary-input",
        role="user",
        message="must not be consumed",
    )
    malformed = (
        "malformed-reconciliation",
        json.dumps(
            {
                "type": "run_reconciliation",
                "run_epoch_id": f"{task_id}:input-1",
            }
        ),
    )
    input_stream = _QueuedInputStream([ordinary, malformed])
    runner, task = _mutation_failure_runner_and_task(
        "succeeded",
        [],
        {"available": True, "status": None, "run_epoch_id": None},
        task_state,
        task_id=task_id,
        sequence_start=1,
        has_input=False,
        input_stream=input_stream,
    )

    await task._execute_task()

    assert input_stream._events == [ordinary, malformed]
    assert await task_state.get_status(task_id) == TaskStatus.PENDING
    assert runner._run_outcome.error.code == "RUN_OUTCOME_UNSET"


@pytest.mark.parametrize(
    ("flow_status", "terminal_status", "authoritative_task_status"),
    [
        ("succeeded", "completed", TaskStatus.DONE),
        ("failed", "failed", TaskStatus.FAILED),
        ("waiting", "waiting", TaskStatus.PENDING),
        ("cancelled", "cancelled", TaskStatus.CANCELLED),
    ],
)
@pytest.mark.asyncio
async def test_cancelled_authoritative_status_mapping_is_recoverable(
    flow_status,
    terminal_status,
    authoritative_task_status,
):
    task_id = f"task-status-cancel-{flow_status}"
    client = _FailingMutationRedisClient(
        task_id,
        cancel_status_once=authoritative_task_status.value,
    )
    client.values[TaskStateService.meta_key(task_id)] = json.dumps(
        {
            "task_id": task_id,
            "session_id": "session-1",
            "status": TaskStatus.RUNNING.value,
        }
    )
    task_state = _production_task_state(client)
    persisted = []
    database_state = {
        "available": True,
        "status": None,
        "run_epoch_id": None,
    }
    runner, task = _mutation_failure_runner_and_task(
        flow_status,
        persisted,
        database_state,
        task_state,
        task_id=task_id,
        sequence_start=1,
        has_input=True,
    )

    await task._execute_task()

    assert runner._run_outcome.status == flow_status
    assert database_state["status"] == terminal_status
    assert await task_state.get_status(task_id) == TaskStatus.PENDING
    reconciliation = await task_state.get_run_reconciliation(task_id)
    assert reconciliation is not None
    assert reconciliation["outcome"]["status"] == flow_status
    assert client.status_attempts == [
        authoritative_task_status.value,
        TaskStatus.PENDING.value,
    ]


@pytest.mark.parametrize(
    ("flow_status", "terminal_status", "authoritative_task_status"),
    [
        ("succeeded", "completed", TaskStatus.DONE),
        ("failed", "failed", TaskStatus.FAILED),
        ("waiting", "waiting", TaskStatus.PENDING),
        ("cancelled", "cancelled", TaskStatus.CANCELLED),
    ],
)
@pytest.mark.asyncio
async def test_cancelled_runner_cleanup_preserves_authoritative_mapping(
    flow_status,
    terminal_status,
    authoritative_task_status,
):
    task_id = f"task-runner-cleanup-cancel-{flow_status}"
    client = _FailingMutationRedisClient(task_id)
    client.values[TaskStateService.meta_key(task_id)] = json.dumps(
        {
            "task_id": task_id,
            "session_id": "session-1",
            "status": TaskStatus.RUNNING.value,
        }
    )
    task_state = _production_task_state(client)
    persisted = []
    database_state = {
        "available": True,
        "status": None,
        "run_epoch_id": None,
    }
    runner, task = _mutation_failure_runner_and_task(
        flow_status,
        persisted,
        database_state,
        task_state,
        task_id=task_id,
        sequence_start=1,
        has_input=True,
    )
    runner._cleanup_tools = AsyncMock(side_effect=asyncio.CancelledError)

    await task._execute_task()

    assert runner._run_outcome.status == flow_status
    assert database_state["status"] == terminal_status
    assert await task_state.get_status(task_id) == authoritative_task_status
    assert await task_state.get_run_reconciliation(task_id) is None


@pytest.mark.parametrize(
    ("flow_status", "terminal_status", "authoritative_task_status"),
    [
        ("succeeded", "completed", TaskStatus.DONE),
        ("failed", "failed", TaskStatus.FAILED),
        ("waiting", "waiting", TaskStatus.PENDING),
        ("cancelled", "cancelled", TaskStatus.CANCELLED),
    ],
)
@pytest.mark.asyncio
async def test_one_shot_authoritative_status_mutation_failure_converges(
    flow_status,
    terminal_status,
    authoritative_task_status,
):
    task_id = f"task-status-once-{flow_status}"
    client = _FailingMutationRedisClient(
        task_id,
        status_failures={authoritative_task_status.value: 1},
    )
    client.values[TaskStateService.meta_key(task_id)] = json.dumps(
        {
            "task_id": task_id,
            "session_id": "session-1",
            "status": TaskStatus.RUNNING.value,
        }
    )
    first_state = _production_task_state(client)
    persisted = []
    database_state = {
        "available": True,
        "status": None,
        "run_epoch_id": None,
    }
    first_runner, first_task = _mutation_failure_runner_and_task(
        flow_status,
        persisted,
        database_state,
        first_state,
        task_id=task_id,
        sequence_start=1,
        has_input=True,
    )

    await first_task._execute_task()

    assert database_state["status"] == terminal_status
    assert await first_state.get_status(task_id) == TaskStatus.PENDING
    reconciliation = await first_state.get_run_reconciliation(task_id)
    assert reconciliation is not None
    assert reconciliation["outcome"]["status"] == flow_status
    assert TaskStatus.DONE.value not in client.status_attempts[1:]
    assert TaskStatus.FAILED.value not in client.status_attempts[1:]

    reconstructed_state = _production_task_state(client)
    fresh_runner, fresh_task = _mutation_failure_runner_and_task(
        "succeeded",
        persisted,
        database_state,
        reconstructed_state,
        task_id=task_id,
        sequence_start=201,
        has_input=False,
    )
    await fresh_task._execute_task()

    assert fresh_runner._run_outcome.status == flow_status
    assert await reconstructed_state.get_status(task_id) == (
        authoritative_task_status
    )
    assert (
        await reconstructed_state.get_run_reconciliation(task_id)
        is None
    )
    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running", terminal_status]


@pytest.mark.asyncio
async def test_fresh_recovery_precedes_sandbox_and_never_replaces_proposal():
    task_id = "task-recovery-before-sandbox"
    client = _FailingMutationRedisClient(
        task_id,
        status_failures={TaskStatus.DONE.value: 2},
    )
    client.values[TaskStateService.meta_key(task_id)] = json.dumps(
        {
            "task_id": task_id,
            "session_id": "session-1",
            "status": TaskStatus.RUNNING.value,
        }
    )
    persisted = []
    database_state = {
        "available": True,
        "status": None,
        "run_epoch_id": None,
    }
    original = RunOutcome.succeeded(usage={"tokens": 7})
    first_state = _production_task_state(client)
    first_runner, first_task = _mutation_failure_runner_and_task(
        "succeeded",
        persisted,
        database_state,
        first_state,
        task_id=task_id,
        sequence_start=1,
        has_input=True,
    )
    first_runner._flow = SimpleNamespace(outcome=original)

    await first_task._execute_task()

    expected_epoch = f"{task_id}:input-mutation"
    expected_proposal = {
        "run_generation": 1,
        "run_epoch_id": expected_epoch,
        "outcome": original.model_dump(mode="json"),
    }
    assert database_state == {
        "available": True,
        "status": "completed",
        "run_epoch_id": expected_epoch,
    }
    assert await first_state.get_status(task_id) == TaskStatus.PENDING
    assert await first_state.get_run_reconciliation(task_id) == (
        expected_proposal
    )

    fresh_state = _production_task_state(client)
    fresh_runner, fresh_task = _mutation_failure_runner_and_task(
        "failed",
        persisted,
        database_state,
        fresh_state,
        task_id=task_id,
        sequence_start=201,
        has_input=False,
    )
    fresh_runner._sandbox_lifecycle.ensure_ready = AsyncMock(
        side_effect=RuntimeError("sandbox unavailable"),
    )

    await fresh_task._execute_task()

    fresh_runner._sandbox_lifecycle.ensure_ready.assert_not_awaited()
    assert fresh_runner._run_epoch_id == expected_epoch
    assert fresh_runner._run_outcome == original
    assert await fresh_state.get_status(task_id) == TaskStatus.PENDING
    assert await fresh_state.get_run_reconciliation(task_id) == (
        expected_proposal
    )
    assert [
        (payload["status"], payload["run_epoch_id"])
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == [
        ("running", expected_epoch),
        ("completed", expected_epoch),
    ]

    converging_state = _production_task_state(client)
    converging_runner, converging_task = _mutation_failure_runner_and_task(
        "failed",
        persisted,
        database_state,
        converging_state,
        task_id=task_id,
        sequence_start=401,
        has_input=False,
    )

    await converging_task._execute_task()

    converging_runner._sandbox_lifecycle.ensure_ready.assert_not_awaited()
    assert converging_runner._run_epoch_id == expected_epoch
    assert converging_runner._run_outcome == original
    assert await converging_state.get_status(task_id) == TaskStatus.DONE
    assert await converging_state.get_run_reconciliation(task_id) is None
    assert [
        (payload["status"], payload["run_epoch_id"])
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == [
        ("running", expected_epoch),
        ("completed", expected_epoch),
    ]


@pytest.mark.parametrize(
    ("flow_status", "terminal_status"),
    [
        ("succeeded", "completed"),
        ("failed", "failed"),
        ("waiting", "waiting"),
        ("cancelled", "cancelled"),
    ],
)
@pytest.mark.asyncio
async def test_persistent_authoritative_status_outage_is_recoverable(
    flow_status,
    terminal_status,
):
    task_id = f"task-status-persistent-{flow_status}"
    client = _FailingMutationRedisClient(
        task_id,
        fail_all_status_mutations=True,
    )
    client.values[TaskStateService.meta_key(task_id)] = json.dumps(
        {
            "task_id": task_id,
            "session_id": "session-1",
            "status": TaskStatus.RUNNING.value,
        }
    )
    task_state = _production_task_state(client)
    persisted = []
    database_state = {
        "available": True,
        "status": None,
        "run_epoch_id": None,
    }
    runner, task = _mutation_failure_runner_and_task(
        flow_status,
        persisted,
        database_state,
        task_state,
        task_id=task_id,
        sequence_start=1,
        has_input=True,
    )

    with pytest.raises(RecoverableTaskReconciliationRequired):
        await task._execute_task()

    assert runner._run_outcome.status == flow_status
    assert database_state["status"] == terminal_status
    assert await task_state.get_status(task_id) == TaskStatus.RUNNING
    reconciliation = await task_state.get_run_reconciliation(task_id)
    assert reconciliation is not None
    assert reconciliation["outcome"]["status"] == flow_status


@pytest.mark.parametrize(
    ("flow_status", "terminal_status", "authoritative_task_status"),
    [
        ("succeeded", "completed", TaskStatus.DONE),
        ("failed", "failed", TaskStatus.FAILED),
        ("waiting", "waiting", TaskStatus.PENDING),
        ("cancelled", "cancelled", TaskStatus.CANCELLED),
    ],
)
@pytest.mark.asyncio
async def test_reconciliation_cleanup_failure_preserves_authoritative_mapping(
    flow_status,
    terminal_status,
    authoritative_task_status,
):
    task_id = f"task-clear-{flow_status}"
    client = _FailingMutationRedisClient(
        task_id,
        fail_clear_persistently=True,
    )
    client.values[TaskStateService.meta_key(task_id)] = json.dumps(
        {
            "task_id": task_id,
            "session_id": "session-1",
            "status": TaskStatus.RUNNING.value,
        }
    )
    task_state = _production_task_state(client)
    persisted = []
    database_state = {
        "available": True,
        "status": None,
        "run_epoch_id": None,
    }
    runner, task = _mutation_failure_runner_and_task(
        flow_status,
        persisted,
        database_state,
        task_state,
        task_id=task_id,
        sequence_start=1,
        has_input=True,
    )

    await task._execute_task()

    assert runner._run_outcome.status == flow_status
    assert database_state["status"] == terminal_status
    assert await task_state.get_status(task_id) == authoritative_task_status
    reconciliation = await task_state.get_run_reconciliation(task_id)
    assert reconciliation is not None
    assert reconciliation["outcome"]["status"] == flow_status


@pytest.mark.parametrize(
    ("flow_status", "terminal_status", "authoritative_task_status"),
    [
        ("succeeded", "completed", TaskStatus.DONE),
        ("failed", "failed", TaskStatus.FAILED),
        ("waiting", "waiting", TaskStatus.PENDING),
        ("cancelled", "cancelled", TaskStatus.CANCELLED),
    ],
)
@pytest.mark.asyncio
async def test_cancelled_cleanup_cannot_overwrite_authoritative_mapping(
    flow_status,
    terminal_status,
    authoritative_task_status,
):
    task_id = f"task-clear-cancel-{flow_status}"
    client = _FailingMutationRedisClient(
        task_id,
        cancel_clear_once=True,
    )
    client.values[TaskStateService.meta_key(task_id)] = json.dumps(
        {
            "task_id": task_id,
            "session_id": "session-1",
            "status": TaskStatus.RUNNING.value,
        }
    )
    task_state = _production_task_state(client)
    persisted = []
    database_state = {
        "available": True,
        "status": None,
        "run_epoch_id": None,
    }
    runner, task = _mutation_failure_runner_and_task(
        flow_status,
        persisted,
        database_state,
        task_state,
        task_id=task_id,
        sequence_start=1,
        has_input=True,
    )

    await task._execute_task()

    assert runner._run_outcome.status == flow_status
    assert database_state["status"] == terminal_status
    assert await task_state.get_status(task_id) == authoritative_task_status
    reconciliation = await task_state.get_run_reconciliation(task_id)
    assert reconciliation is not None
    assert reconciliation["outcome"]["status"] == flow_status


@pytest.mark.asyncio
async def test_same_runner_recovers_terminal_without_requiring_new_input():
    persisted = []
    database_state = {
        "available": False,
        "status": None,
        "run_epoch_id": None,
    }
    task_state = _DurableTaskState()
    runner, task = _recoverable_runner_and_task(
        "succeeded",
        persisted,
        database_state,
        task_state,
        sequence_start=1,
        has_input=True,
    )

    await task._execute_task()
    assert task_state.status == TaskStatus.PENDING
    assert task_state.reconciliation == {
        "run_generation": 1,
        "run_epoch_id": "task-recovery:input-recovery",
        "outcome": {
            "status": "succeeded",
            "error": None,
            "usage": {},
        },
    }

    database_state["available"] = True
    await task._execute_task()

    assert task_state.status == TaskStatus.DONE
    assert task_state.reconciliation is None
    assert runner._run_outcome.status == "succeeded"
    assert runner.terminal_status == SessionStatus.COMPLETED
    assert database_state["status"] == "completed"
    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running", "completed"]
    runner._on_complete_callback.assert_awaited_once_with("session-1")


@pytest.mark.asyncio
async def test_fresh_runner_recovers_failed_terminal_without_new_input():
    persisted = []
    database_state = {
        "available": False,
        "status": None,
        "run_epoch_id": None,
    }
    task_state = _DurableTaskState()
    first_runner, first_task = _recoverable_runner_and_task(
        "failed",
        persisted,
        database_state,
        task_state,
        sequence_start=1,
        has_input=True,
    )
    await first_task._execute_task()
    assert task_state.status == TaskStatus.PENDING
    assert task_state.reconciliation["outcome"] == {
        "status": "failed",
        "error": {
            "message": "agent failed",
            "code": "AGENT_FAILED",
        },
        "usage": {},
    }

    database_state["available"] = True
    fresh_runner, fresh_task = _recoverable_runner_and_task(
        "succeeded",
        persisted,
        database_state,
        task_state,
        sequence_start=201,
        has_input=False,
    )
    await fresh_task._execute_task()

    assert task_state.status == TaskStatus.FAILED
    assert task_state.reconciliation is None
    assert fresh_runner._run_outcome.status == "failed"
    assert fresh_runner._run_outcome.error.code == "AGENT_FAILED"
    assert fresh_runner.terminal_status == SessionStatus.FAILED
    assert database_state["status"] == "failed"
    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running", "failed"]


@pytest.mark.asyncio
async def test_heartbeat_race_preserves_proposal_for_fresh_runner_recovery():
    persisted = []
    database_state = {
        "available": False,
        "status": None,
        "run_epoch_id": None,
    }
    task_id = "task-recovery"
    client = _RacingRedisClient(task_id)
    client.values[TaskStateService.meta_key(task_id)] = json.dumps(
        {
            "task_id": task_id,
            "session_id": "session-1",
            "status": TaskStatus.RUNNING.value,
            "worker_id": "",
        }
    )
    first_state = _production_task_state(client)
    first_runner, first_task = _recoverable_runner_and_task(
        "failed",
        persisted,
        database_state,
        first_state,
        sequence_start=1,
        has_input=True,
    )

    first_input = True

    async def pop_first_input(_task):
        nonlocal first_input
        if not first_input:
            return None
        first_input = False
        client.race_started.set()
        return MessageEvent(
            id="input-recovery",
            role="user",
            message="recover this run",
        )

    first_runner._pop_event = pop_first_input

    async def heartbeat_after_input():
        await client.race_started.wait()
        await first_state.record_heartbeat(task_id, 1, "worker-race")

    heartbeat = asyncio.create_task(
        heartbeat_after_input()
    )
    await first_task._execute_task()
    await heartbeat

    database_state["available"] = True
    reconstructed_state = _production_task_state(client)
    fresh_runner, fresh_task = _recoverable_runner_and_task(
        "succeeded",
        persisted,
        database_state,
        reconstructed_state,
        sequence_start=201,
        has_input=False,
    )
    await fresh_task._execute_task()

    assert database_state == {
        "available": True,
        "status": "failed",
        "run_epoch_id": "task-recovery:input-recovery",
    }
    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running", "failed"]
    assert fresh_runner._run_outcome.status == "failed"
    assert fresh_runner.terminal_status == SessionStatus.FAILED
    assert await reconstructed_state.get_status(task_id) == TaskStatus.FAILED
    assert await reconstructed_state.get_run_reconciliation(task_id) is None
    recovered_meta = await reconstructed_state.get_task_meta(task_id)
    assert recovered_meta["session_id"] == "session-1"
    assert recovered_meta["worker_id"] == "worker-race"
    assert client.expiries[TaskStateService.meta_key(task_id)] == 86400 * 7


class _RejectPresentationOutputStream(_OutputStream):
    async def put(self, event_json):
        payload = json.loads(event_json)
        if payload.get("type") != "session_status":
            raise RuntimeError("presentation stream unavailable")
        return await super().put(event_json)


class _FailOrdinaryPersistenceUow(_EmitterUow):
    async def add_event_payloads(self, _session_id, _payloads):
        raise RuntimeError("ordinary event database unavailable")


class _ProjectionFailureUow:
    def __init__(self, failure_mode):
        self.session = self
        self._failure_mode = failure_mode

    async def update_title(self, *_args):
        if self._failure_mode == "title_update":
            raise RuntimeError("title projection unavailable")

    async def update_latest_message(self, *_args):
        if self._failure_mode == "latest_message":
            raise RuntimeError("latest-message projection unavailable")

    async def increment_unread_message_count(self, *_args):
        if self._failure_mode == "unread":
            raise RuntimeError("unread projection unavailable")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        if self._failure_mode == "commit":
            raise RuntimeError("projection commit unavailable")
        return False


def _projection_failure_runner_and_task(event, failure_mode):
    runner, _unused_task, _statuses = _runner_for_outcome("succeeded")
    persisted = []
    task_state = _DurableTaskState()
    runner._event_emitter = AgentEventEmitter(
        session_id="session-1",
        uow_factory=lambda: _EmitterUow(persisted),
        event_sequence=SimpleNamespace(
            allocate=AsyncMock(side_effect=range(1, 200))
        ),
        task_state_port=task_state,
    )
    runner._task_state_port = task_state
    runner._uow_factory = lambda: _ProjectionFailureUow(failure_mode)
    runner._on_complete_callback = None
    runner._on_session_terminal_callback = None
    runner._flow = SimpleNamespace(outcome=RunOutcome.succeeded())

    async def run_flow(_message):
        yield event

    runner._run_flow = run_flow
    for shadowed_method in (
        "_emit_session_status",
        "_put_and_add_event",
        "_flush_event_persist_buffer",
    ):
        if hasattr(runner, shadowed_method):
            delattr(runner, shadowed_method)

    task = RedisStreamTask.__new__(RedisStreamTask)
    task._id = f"task-projection-{failure_mode}"
    task._session_id = "session-1"
    task._task_runner = runner
    task._input_stream = _InputStream()
    task._output_stream = _OutputStream()
    task._task_state = task_state
    return runner, task, task_state, persisted


@pytest.mark.parametrize(
    ("event", "failure_mode"),
    [
        (TitleEvent(title="Projected title"), "title_update"),
        (
            MessageEvent(role="assistant", message="Projected response"),
            "latest_message",
        ),
        (
            AssistantNoticeEvent(message="Projected notice"),
            "latest_message",
        ),
        (MessageEvent(role="assistant", message="Unread response"), "unread"),
        (AssistantNoticeEvent(message="Commit notice"), "commit"),
    ],
)
@pytest.mark.asyncio
async def test_projection_failure_cannot_reclassify_success(
    event,
    failure_mode,
):
    runner, task, task_state, persisted = _projection_failure_runner_and_task(
        event,
        failure_mode,
    )

    await task._execute_task()

    assert runner._run_outcome.status == "succeeded"
    assert runner.terminal_status == SessionStatus.COMPLETED
    assert task_state.status == TaskStatus.DONE
    assert [
        payload["status"]
        for persisted_event, payload in persisted
        if isinstance(persisted_event, SessionStatusEvent)
    ] == ["running", "completed"]


def _presentation_failure_runner_and_task(
    flow_status,
    failure_mode,
    *,
    cancellation_mode=None,
):
    runner, _unused_task, _statuses = _runner_for_outcome(
        flow_status,
        cancelled=cancellation_mode == "cooperative",
    )
    persisted = []
    task_state = _DurableTaskState()
    uow_cls = (
        _FailOrdinaryPersistenceUow
        if failure_mode == "database"
        else _EmitterUow
    )
    runner._event_emitter = AgentEventEmitter(
        session_id="session-1",
        uow_factory=lambda: uow_cls(persisted),
        event_sequence=SimpleNamespace(
            allocate=AsyncMock(side_effect=range(1, 200))
        ),
        task_state_port=task_state,
    )
    runner._task_state_port = task_state
    runner._on_complete_callback = None
    runner._on_session_terminal_callback = None
    outcome = {
        "succeeded": RunOutcome.succeeded(),
        "failed": RunOutcome.failed("flow failed", code="FLOW_FAILED"),
        "waiting": RunOutcome.waiting(),
    }[flow_status]
    runner._flow = SimpleNamespace(outcome=outcome)

    if cancellation_mode == "asynchronous":
        runner._flow = _CancellingFlow()
        del runner._run_flow
    elif cancellation_mode is None:
        presentation_event = {
            "succeeded": DoneEvent(),
            "failed": ErrorEvent(error="flow failed"),
            "waiting": WaitEvent(reason="approval"),
        }[flow_status]

        async def _run_flow(_message):
            yield presentation_event

        runner._run_flow = _run_flow

    for shadowed_method in (
        "_emit_session_status",
        "_put_and_add_event",
        "_flush_event_persist_buffer",
    ):
        if hasattr(runner, shadowed_method):
            delattr(runner, shadowed_method)

    task = RedisStreamTask.__new__(RedisStreamTask)
    task._id = f"task-presentation-{flow_status}-{failure_mode}"
    task._session_id = "session-1"
    task._task_runner = runner
    task._input_stream = _InputStream()
    task._output_stream = (
        _RejectPresentationOutputStream()
        if failure_mode == "stream"
        else _OutputStream()
    )
    task._task_state = task_state
    return runner, task, task_state, persisted


@pytest.mark.parametrize(
    ("flow_status", "expected_session", "expected_task"),
    [
        ("succeeded", "completed", TaskStatus.DONE),
        ("failed", "failed", TaskStatus.FAILED),
        ("waiting", "waiting", TaskStatus.PENDING),
    ],
)
@pytest.mark.parametrize("failure_mode", ["stream", "database"])
@pytest.mark.asyncio
async def test_presentation_failure_cannot_prevent_explicit_outcome(
    flow_status,
    expected_session,
    expected_task,
    failure_mode,
):
    runner, task, task_state, persisted = (
        _presentation_failure_runner_and_task(
            flow_status,
            failure_mode,
        )
    )

    try:
        await task._execute_task()
    except RuntimeError:
        pass

    assert task_state.status == expected_task
    assert runner._run_outcome.status == flow_status
    assert runner.terminal_status == SessionStatus(expected_session)
    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running", expected_session]


@pytest.mark.parametrize("failure_mode", ["stream", "database"])
@pytest.mark.parametrize(
    "cancellation_mode",
    ["cooperative", "asynchronous"],
)
@pytest.mark.asyncio
async def test_presentation_failure_cannot_reclassify_cancellation(
    failure_mode,
    cancellation_mode,
):
    runner, task, task_state, persisted = (
        _presentation_failure_runner_and_task(
            "succeeded",
            failure_mode,
            cancellation_mode=cancellation_mode,
        )
    )

    try:
        await task._execute_task()
    except (asyncio.CancelledError, RuntimeError):
        pass

    assert task_state.status == TaskStatus.CANCELLED
    assert task_state.reconciliation is None
    assert runner._run_outcome.status == "cancelled"
    assert runner.terminal_status == SessionStatus.CANCELLED
    assert [
        payload["status"]
        for event, payload in persisted
        if isinstance(event, SessionStatusEvent)
    ] == ["running", "cancelled"]
