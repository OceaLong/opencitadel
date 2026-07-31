#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.event import MessageEvent, SessionStatusEvent
from app.domain.models.resource_governance import (
    ResourceBindingProjection,
    ResourceKind,
)
from app.domain.services.agent.event_emitter import AgentEventEmitter
from app.interfaces.schemas.event import EventMapper


_EPOCH_TERMINALS = {"waiting", "completed", "cancelled", "failed"}


class _SharedStatusStore:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.run_epoch_id = None
        self.terminal_status = None
        self.persisted = []
        self.claim_barrier = None
        self.fail_claims = 0
        self.cancel_uow_exit_once = False

    async def claim(self, session_id, event, payload):
        if self.fail_claims:
            self.fail_claims -= 1
            raise RuntimeError("terminal database flush failed")
        if (
            event.status in _EPOCH_TERMINALS
            and self.claim_barrier is not None
        ):
            await self.claim_barrier.wait()
        async with self.lock:
            if event.status == "running":
                if self.run_epoch_id == event.run_epoch_id:
                    return False
                self.run_epoch_id = event.run_epoch_id
                self.terminal_status = None
            elif event.status in _EPOCH_TERMINALS:
                if (
                    event.run_epoch_id != self.run_epoch_id
                    or self.terminal_status is not None
                ):
                    return False
                self.terminal_status = event.status
            self.persisted.append((event, dict(payload)))
            return True


class _StatusRepository:
    def __init__(self, store: _SharedStatusStore) -> None:
        self._store = store

    async def claim_session_status_event(self, session_id, event, payload):
        return await self._store.claim(session_id, event, payload)

    async def add_event_payloads(self, _session_id, payloads):
        self._store.persisted.extend(payloads)

    async def list_events(
        self,
        _session_id,
        *,
        before=None,
        limit=100,
        latest=False,
    ):
        records = [
            (int(payload["id"]), event)
            for event, payload in self._store.persisted
        ]
        if before is not None:
            records = [record for record in records if record[0] < before]
        if latest or before is not None:
            return records[-limit:]
        return records[:limit]


class _Uow:
    def __init__(self, store: _SharedStatusStore) -> None:
        self.session = _StatusRepository(store)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        if self.session._store.cancel_uow_exit_once:
            self.session._store.cancel_uow_exit_once = False
            raise asyncio.CancelledError
        return False


class _OutputStream:
    def __init__(self, *, fail_after_publish=False) -> None:
        self.events = []
        self._fail_after_publish = fail_after_publish

    async def put(self, event_json):
        self.events.append(event_json)
        if self._fail_after_publish:
            self._fail_after_publish = False
            raise RuntimeError("stream failed after publication")
        return f"stream-{len(self.events)}"


def _emitter(
    store: _SharedStatusStore,
    *,
    output_stream=None,
    task_state=None,
    start_seq=1,
):
    emitter = AgentEventEmitter(
        session_id="session-1",
        uow_factory=lambda: _Uow(store),
        event_sequence=SimpleNamespace(
            allocate=AsyncMock(side_effect=range(start_seq, start_seq + 100))
        ),
        task_state_port=task_state
        or SimpleNamespace(set_output_seq_cursor=AsyncMock()),
    )
    task = SimpleNamespace(
        id="task-1",
        output_stream=output_stream or _OutputStream(),
    )
    return emitter, task


@pytest.mark.asyncio
async def test_two_emitters_atomically_claim_one_terminal_for_same_epoch():
    store = _SharedStatusStore()
    starter, starter_task = _emitter(store, start_seq=1)
    assert await starter.emit(
        starter_task,
        SessionStatusEvent(status="running", run_epoch_id="task-1:input-1"),
    )

    ready = 0
    release = asyncio.Event()

    class _TwoReaderBarrier:
        async def wait(self):
            nonlocal ready
            ready += 1
            if ready == 2:
                release.set()
            await release.wait()

    store.claim_barrier = _TwoReaderBarrier()
    first, first_task = _emitter(store, start_seq=10)
    second, second_task = _emitter(store, start_seq=20)

    accepted = await asyncio.gather(
        first.emit(
            first_task,
            SessionStatusEvent(
                status="failed",
                run_epoch_id="task-1:input-1",
            ),
        ),
        second.emit(
            second_task,
            SessionStatusEvent(
                status="completed",
                run_epoch_id="task-1:input-1",
            ),
        ),
    )

    assert sorted(accepted) == [False, True]
    assert [
        payload["status"]
        for _event, payload in store.persisted
        if payload["status"] != "running"
    ] in (["failed"], ["completed"])


@pytest.mark.asyncio
async def test_status_is_durable_before_stream_publication_failure():
    store = _SharedStatusStore()
    emitter, task = _emitter(
        store,
        output_stream=_OutputStream(fail_after_publish=True),
    )

    accepted = await emitter.emit(
        task,
        SessionStatusEvent(status="running", run_epoch_id="task-1:input-1"),
    )

    assert accepted is True
    assert [payload["status"] for _event, payload in store.persisted] == [
        "running"
    ]


@pytest.mark.asyncio
async def test_cursor_failure_cannot_reopen_a_durable_terminal():
    store = _SharedStatusStore()
    task_state = SimpleNamespace(
        set_output_seq_cursor=AsyncMock(
            side_effect=RuntimeError("cursor unavailable")
        )
    )
    emitter, task = _emitter(store, task_state=task_state, start_seq=1)
    await emitter.emit(
        task,
        SessionStatusEvent(status="running", run_epoch_id="task-1:input-1"),
    )
    accepted = await emitter.emit(
        task,
        SessionStatusEvent(status="cancelled", run_epoch_id="task-1:input-1"),
    )

    fresh, fresh_task = _emitter(store, start_seq=20)
    late = await fresh.emit(
        fresh_task,
        SessionStatusEvent(status="failed", run_epoch_id="task-1:input-1"),
    )

    assert accepted is True
    assert late is False
    assert store.terminal_status == "cancelled"


@pytest.mark.asyncio
async def test_terminal_database_failure_is_retryable_before_fresh_replay():
    store = _SharedStatusStore()
    emitter, task = _emitter(store, start_seq=1)
    await emitter.emit(
        task,
        SessionStatusEvent(status="running", run_epoch_id="task-1:input-1"),
    )
    store.fail_claims = 1

    with pytest.raises(RuntimeError, match="terminal database flush failed"):
        await emitter.emit(
            task,
            SessionStatusEvent(
                status="cancelled",
                run_epoch_id="task-1:input-1",
            ),
        )
    await emitter.flush()

    fresh, fresh_task = _emitter(store, start_seq=20)
    assert await fresh.emit(
        fresh_task,
        SessionStatusEvent(status="failed", run_epoch_id="task-1:input-1"),
    ) is False
    assert store.terminal_status == "cancelled"


@pytest.mark.asyncio
async def test_cancelled_commit_is_reconciled_by_a_subsequent_authoritative_read():
    store = _SharedStatusStore()
    emitter, task = _emitter(store, start_seq=1)
    await emitter.emit(
        task,
        SessionStatusEvent(status="running", run_epoch_id="task-1:input-1"),
    )
    store.cancel_uow_exit_once = True

    with pytest.raises(asyncio.CancelledError):
        await emitter.emit(
            task,
            SessionStatusEvent(
                status="completed",
                run_epoch_id="task-1:input-1",
            ),
        )

    assert len(task.output_stream.events) == 1
    await emitter.flush()
    assert emitter.claimed_terminal_status == "completed"
    assert len(task.output_stream.events) == 1


def _binding_projection(version_id: str) -> ResourceBindingProjection:
    return ResourceBindingProjection(
        binding_id=f"binding-{version_id}",
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id="kb1",
        version_id=version_id,
    )


@pytest.mark.asyncio
async def test_turn_binding_snapshot_is_applied_to_message_and_status_events():
    store = _SharedStatusStore()
    emitter, task = _emitter(store, start_seq=1)
    emitter.start_turn([_binding_projection("kbv1")])

    await emitter.emit(
        task,
        SessionStatusEvent(
            status="running",
            run_epoch_id="task-1:input-1",
        ),
    )
    await emitter.emit(
        task,
        MessageEvent(role="assistant", message="answer"),
    )
    await emitter.flush()

    assert [
        payload["resource_bindings"][0]["version_id"]
        for _event, payload in store.persisted
    ] == ["kbv1", "kbv1"]


@pytest.mark.asyncio
async def test_explicit_empty_bindings_preserve_historical_snapshot_across_event_paths():
    store = _SharedStatusStore()
    emitter, task = _emitter(store, start_seq=1)
    emitter.start_turn([_binding_projection("kbv2")])
    status = SessionStatusEvent(
        status="running",
        run_epoch_id="task-1:input-1",
        resource_bindings=[],
    )
    message = MessageEvent(
        role="assistant",
        message="historical answer",
        resource_bindings=[],
    )

    await emitter.emit(task, status)
    await emitter.emit(task, message)
    await emitter.flush()

    assert status.resource_bindings == []
    assert message.resource_bindings == []
    assert [
        payload["resource_bindings"]
        for _event, payload in store.persisted
    ] == [[], []]
    assert [
        json.loads(payload)["resource_bindings"]
        for payload in task.output_stream.events
    ] == [[], []]
    assert EventMapper.event_to_sse_event(message).data.resource_bindings == []


@pytest.mark.asyncio
async def test_starting_new_turn_never_rewrites_prior_event_binding_snapshot():
    store = _SharedStatusStore()
    emitter, task = _emitter(store, start_seq=1)
    emitter.start_turn([_binding_projection("kbv1")])
    first = MessageEvent(role="assistant", message="old answer")
    await emitter.emit(task, first)
    await emitter.flush()
    first_payload = dict(store.persisted[0][1])

    emitter.start_turn([_binding_projection("kbv2")])
    await emitter.emit(
        task,
        MessageEvent(role="assistant", message="new answer"),
    )
    await emitter.flush()

    assert first.resource_bindings[0].version_id == "kbv1"
    assert first_payload["resource_bindings"][0]["version_id"] == "kbv1"
    assert store.persisted[0][1] == first_payload
    assert store.persisted[1][1]["resource_bindings"][0]["version_id"] == "kbv2"


@pytest.mark.asyncio
async def test_turn_binding_snapshots_do_not_leak_between_emitters():
    first_store = _SharedStatusStore()
    second_store = _SharedStatusStore()
    first, first_task = _emitter(first_store, start_seq=1)
    second, second_task = _emitter(second_store, start_seq=20)
    first.start_turn([_binding_projection("kbv1")])
    second.start_turn([_binding_projection("kbv2")])

    await asyncio.gather(
        first.emit(
            first_task,
            MessageEvent(role="assistant", message="first"),
        ),
        second.emit(
            second_task,
            MessageEvent(role="assistant", message="second"),
        ),
    )
    await asyncio.gather(first.flush(), second.flush())

    assert (
        first_store.persisted[0][1]["resource_bindings"][0]["version_id"]
        == "kbv1"
    )
    assert (
        second_store.persisted[0][1]["resource_bindings"][0]["version_id"]
        == "kbv2"
    )
