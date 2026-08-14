#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CheckpointService: create_checkpoint / restore / list_checkpoints /
resume_latest_checkpoint had zero direct coverage before this file.

Fake shapes for session/checkpoint repos and sandbox are hand-built from the
protocols (SessionRepository, CheckpointRepository, Sandbox) since no
existing test doubles cover this surface; the `_FakeCheckpointService`
consumer stub in test_governance_profile_service.py only exercises
`list_checkpoints` at the boundary and doesn't model the internals needed
here.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.domain.models.checkpoint import Checkpoint, CheckpointAnchorType, SessionStateSnapshot
from app.domain.models.event import MessageEvent
from app.domain.models.session import Session, SessionStatus
from app.domain.services.checkpoint_service import CheckpointService


class _FakeSessionRepo:
    def __init__(self, sessions: Optional[List[Session]] = None) -> None:
        self.sessions: Dict[str, Session] = {s.id: s for s in (sessions or [])}
        self.events_by_session: Dict[str, List[Tuple[int, Any]]] = {}
        self.max_seq = 5
        self.anchor_seq: Optional[int] = 4
        self.delete_events_calls: List[tuple] = []
        self.restore_snapshot_calls: List[dict] = []
        self.latest_message_calls: List[tuple] = []
        self.status_updates: List[tuple] = []

    async def get_by_id(self, session_id: str, scope=None):
        return self.sessions.get(session_id)

    async def save(self, session: Session) -> None:
        self.sessions[session.id] = session

    async def get_max_event_seq(self, session_id: str) -> Optional[int]:
        return self.max_seq

    async def get_event_seq_by_stream_id(self, session_id: str, stream_id: str) -> Optional[int]:
        return self.anchor_seq

    async def delete_events_from_seq(self, session_id: str, from_seq: int, inclusive: bool = True) -> int:
        self.delete_events_calls.append((session_id, from_seq, inclusive))
        return 1

    async def restore_session_snapshot(self, session_id, memories, files, status, pending_phase) -> None:
        self.restore_snapshot_calls.append(
            {"session_id": session_id, "memories": memories, "files": files, "status": status, "pending_phase": pending_phase}
        )

    async def list_events(self, session_id: str, after=None, before=None, limit: int = 100, latest: bool = False):
        return self.events_by_session.get(session_id, [])

    async def update_latest_message(self, session_id: str, message: str, timestamp: datetime) -> None:
        self.latest_message_calls.append((session_id, message, timestamp))

    async def update_status(self, session_id: str, status: SessionStatus) -> None:
        self.status_updates.append((session_id, status))
        session = self.sessions.get(session_id)
        if session:
            session.status = status


class _FakeCheckpointRepo:
    def __init__(self, checkpoints: Optional[List[Checkpoint]] = None) -> None:
        self.checkpoints: Dict[str, Checkpoint] = {cp.id: cp for cp in (checkpoints or [])}
        self.saved: List[Checkpoint] = []
        self.delete_from_calls: List[tuple] = []

    async def save(self, checkpoint: Checkpoint) -> None:
        self.checkpoints[checkpoint.id] = checkpoint
        self.saved.append(checkpoint)

    async def get_by_id(self, checkpoint_id: str) -> Optional[Checkpoint]:
        return self.checkpoints.get(checkpoint_id)

    async def list_by_session(self, session_id: str) -> List[Checkpoint]:
        return [cp for cp in self.checkpoints.values() if cp.session_id == session_id]

    async def delete_from(self, session_id: str, from_created_at: datetime, inclusive: bool = True) -> None:
        self.delete_from_calls.append((session_id, from_created_at, inclusive))


class _FakeObjectStorage:
    def __init__(self) -> None:
        self.blobs: Dict[str, bytes] = {}

    async def put_bytes(self, key: str, data: bytes) -> None:
        self.blobs[key] = data

    async def get_bytes(self, key: str) -> bytes:
        return self.blobs.get(key, b"")

    async def delete_bytes(self, key: str) -> None:
        self.blobs.pop(key, None)


class _FakeSandbox:
    def __init__(self, sandbox_id: str = "sandbox-1", *, fail_workspace: bool = False, fail_browser: bool = False) -> None:
        self._id = sandbox_id
        self.fail_workspace = fail_workspace
        self.fail_browser = fail_browser
        self.ensure_calls = 0
        self.restore_workspace_calls: List[tuple] = []
        self.restore_browser_calls: List[tuple] = []

    @property
    def id(self) -> str:
        return self._id

    async def ensure_sandbox(self) -> None:
        self.ensure_calls += 1

    async def create_workspace_snapshot(self, checkpoint_id: str) -> bytes:
        if self.fail_workspace:
            raise RuntimeError("workspace snapshot failed")
        return b"workspace-bytes"

    async def create_browser_profile_snapshot(self, checkpoint_id: str) -> bytes:
        if self.fail_browser:
            raise RuntimeError("browser snapshot failed")
        return b"browser-bytes"

    async def restore_workspace_snapshot(self, checkpoint_id: str, snapshot_data) -> None:
        self.restore_workspace_calls.append((checkpoint_id, snapshot_data.read()))

    async def restore_browser_profile_snapshot(self, checkpoint_id: str, snapshot_data) -> None:
        self.restore_browser_calls.append((checkpoint_id, snapshot_data.read()))


class _FakeSandboxCls:
    """Classmethod-based sandbox factory mirroring the real `Sandbox.get`/`create` protocol."""

    instances: Dict[str, _FakeSandbox] = {}
    create_calls = 0

    @classmethod
    def reset(cls, instances: Optional[Dict[str, _FakeSandbox]] = None) -> None:
        cls.instances = instances or {}
        cls.create_calls = 0

    @classmethod
    async def get(cls, sandbox_id: str) -> Optional[_FakeSandbox]:
        return cls.instances.get(sandbox_id)

    @classmethod
    async def create(cls) -> _FakeSandbox:
        cls.create_calls += 1
        sandbox = _FakeSandbox("sandbox-created")
        cls.instances[sandbox.id] = sandbox
        return sandbox


class _FakeTaskStatePort:
    def __init__(self) -> None:
        self.cancel_calls: List[str] = []

    async def request_cancel(self, task_id: str) -> None:
        self.cancel_calls.append(task_id)


class _Env:
    def __init__(self, *, sessions=None, checkpoints=None, sandbox_instances=None) -> None:
        self.session_repo = _FakeSessionRepo(sessions)
        self.checkpoint_repo = _FakeCheckpointRepo(checkpoints)
        self.object_storage = _FakeObjectStorage()
        self.task_state = _FakeTaskStatePort()
        _FakeSandboxCls.reset(sandbox_instances)
        self.sandbox_cls = _FakeSandboxCls
        self.service = CheckpointService(
            uow_factory=self._uow_factory,
            object_storage=self.object_storage,
            sandbox_cls=self.sandbox_cls,
            task_state_port=self.task_state,
        )

    def _uow_factory(self):
        return _FakeUow(self.session_repo, self.checkpoint_repo)


class _FakeUow:
    def __init__(self, session_repo, checkpoint_repo) -> None:
        self.session = session_repo
        self.checkpoint = checkpoint_repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _session(**overrides) -> Session:
    fields = dict(id="session-1", title="s", owner_user_id="user-1")
    fields.update(overrides)
    return Session(**fields)


# ---------------------------------------------------------------------------
# create_checkpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_checkpoint_without_sandbox_has_no_snapshot_keys():
    env = _Env(sessions=[_session()])

    checkpoint = await env.service.create_checkpoint(
        "session-1", CheckpointAnchorType.STEP, "evt-1", "before edit"
    )

    assert checkpoint.session_id == "session-1"
    assert checkpoint.sandbox_snapshot_key is None
    assert checkpoint.browser_snapshot_key is None
    assert checkpoint.seq_before == 5
    assert env.checkpoint_repo.checkpoints[checkpoint.id] is checkpoint


@pytest.mark.asyncio
async def test_create_checkpoint_missing_session_raises_value_error():
    env = _Env(sessions=[])

    with pytest.raises(ValueError, match="不存在"):
        await env.service.create_checkpoint("no-such-session", CheckpointAnchorType.STEP, "evt-1", "label")


@pytest.mark.asyncio
async def test_create_checkpoint_with_sandbox_and_operator_scope_snapshots_both_workspace_and_browser():
    """Key path: browser-profile branch only fires when operator_scope is set."""
    sandbox = _FakeSandbox("sandbox-1")
    env = _Env(
        sessions=[_session(sandbox_id="sandbox-1", operator_scope="owned")],
        sandbox_instances={"sandbox-1": sandbox},
    )

    checkpoint = await env.service.create_checkpoint(
        "session-1", CheckpointAnchorType.STEP, "evt-1", "label"
    )

    assert checkpoint.sandbox_snapshot_key == f"checkpoints/session-1/{checkpoint.id}.tgz"
    assert checkpoint.browser_snapshot_key == f"checkpoints/session-1/{checkpoint.id}_browser.tgz"
    assert env.object_storage.blobs[checkpoint.sandbox_snapshot_key] == b"workspace-bytes"
    assert env.object_storage.blobs[checkpoint.browser_snapshot_key] == b"browser-bytes"
    assert sandbox.ensure_calls == 1


@pytest.mark.asyncio
async def test_create_checkpoint_with_sandbox_but_no_operator_scope_skips_browser_snapshot():
    sandbox = _FakeSandbox("sandbox-1")
    env = _Env(
        sessions=[_session(sandbox_id="sandbox-1", operator_scope=None)],
        sandbox_instances={"sandbox-1": sandbox},
    )

    checkpoint = await env.service.create_checkpoint(
        "session-1", CheckpointAnchorType.STEP, "evt-1", "label"
    )

    assert checkpoint.sandbox_snapshot_key is not None
    assert checkpoint.browser_snapshot_key is None


@pytest.mark.asyncio
async def test_create_checkpoint_workspace_snapshot_failure_is_swallowed():
    """A sandbox failure must not abort checkpoint creation — it degrades to
    a conversation-state-only checkpoint (session.memories/files still
    saved), matching the `logger.warning` + continue behavior."""
    sandbox = _FakeSandbox("sandbox-1", fail_workspace=True)
    env = _Env(
        sessions=[_session(sandbox_id="sandbox-1", operator_scope="owned")],
        sandbox_instances={"sandbox-1": sandbox},
    )

    checkpoint = await env.service.create_checkpoint(
        "session-1", CheckpointAnchorType.STEP, "evt-1", "label"
    )

    assert checkpoint.sandbox_snapshot_key is None
    # Browser snapshot still attempted independently of the workspace failure.
    assert checkpoint.browser_snapshot_key is not None


@pytest.mark.asyncio
async def test_create_checkpoint_browser_snapshot_failure_keeps_workspace_snapshot():
    sandbox = _FakeSandbox("sandbox-1", fail_browser=True)
    env = _Env(
        sessions=[_session(sandbox_id="sandbox-1", operator_scope="owned")],
        sandbox_instances={"sandbox-1": sandbox},
    )

    checkpoint = await env.service.create_checkpoint(
        "session-1", CheckpointAnchorType.STEP, "evt-1", "label"
    )

    assert checkpoint.sandbox_snapshot_key is not None
    assert checkpoint.browser_snapshot_key is None


@pytest.mark.asyncio
async def test_create_checkpoint_uses_explicit_sandbox_over_session_sandbox_id():
    explicit_sandbox = _FakeSandbox("explicit")
    session_sandbox = _FakeSandbox("session-sandbox")
    env = _Env(
        sessions=[_session(sandbox_id="session-sandbox")],
        sandbox_instances={"session-sandbox": session_sandbox},
    )

    checkpoint = await env.service.create_checkpoint(
        "session-1", CheckpointAnchorType.STEP, "evt-1", "label", sandbox=explicit_sandbox
    )

    assert checkpoint.sandbox_snapshot_key is not None
    assert session_sandbox.ensure_calls == 0
    assert explicit_sandbox.ensure_calls == 1


@pytest.mark.asyncio
async def test_create_checkpoint_truncates_label_to_500_chars():
    env = _Env(sessions=[_session()])

    checkpoint = await env.service.create_checkpoint(
        "session-1", CheckpointAnchorType.STEP, "evt-1", "x" * 600
    )

    assert len(checkpoint.label) == 500


# ---------------------------------------------------------------------------
# list_checkpoints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_checkpoints_returns_only_session_checkpoints():
    cp1 = Checkpoint(id="cp-1", session_id="session-1", anchor_type=CheckpointAnchorType.STEP, anchor_event_id="e1")
    cp2 = Checkpoint(id="cp-2", session_id="session-2", anchor_type=CheckpointAnchorType.STEP, anchor_event_id="e2")
    env = _Env(sessions=[_session()], checkpoints=[cp1, cp2])

    result = await env.service.list_checkpoints("session-1")

    assert [cp.id for cp in result] == ["cp-1"]


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------

def _checkpoint(**overrides) -> Checkpoint:
    fields = dict(
        id="cp-1",
        session_id="session-1",
        anchor_type=CheckpointAnchorType.STEP,
        anchor_event_id="evt-1",
        session_state=SessionStateSnapshot(status="pending", pending_phase=None),
    )
    fields.update(overrides)
    return Checkpoint(**fields)


@pytest.mark.asyncio
async def test_restore_missing_session_raises_value_error():
    env = _Env(sessions=[], checkpoints=[_checkpoint()])

    with pytest.raises(ValueError, match="不存在"):
        await env.service.restore("session-1", "cp-1")


@pytest.mark.asyncio
async def test_restore_missing_checkpoint_raises_value_error():
    env = _Env(sessions=[_session()], checkpoints=[])

    with pytest.raises(ValueError, match="还原点"):
        await env.service.restore("session-1", "no-such-checkpoint")


@pytest.mark.asyncio
async def test_restore_checkpoint_session_mismatch_raises_value_error():
    """A checkpoint from a different session must never be applied — the
    lookup-by-id result is cross-checked against the requested session_id."""
    checkpoint = _checkpoint(session_id="other-session")
    env = _Env(sessions=[_session()], checkpoints=[checkpoint])

    with pytest.raises(ValueError, match="还原点"):
        await env.service.restore("session-1", "cp-1")


@pytest.mark.asyncio
async def test_restore_success_deletes_events_and_restores_snapshot():
    env = _Env(sessions=[_session(status=SessionStatus.COMPLETED)], checkpoints=[_checkpoint()])
    env.session_repo.anchor_seq = 4

    await env.service.restore("session-1", "cp-1")

    assert env.session_repo.delete_events_calls == [("session-1", 4, True)]
    assert len(env.session_repo.restore_snapshot_calls) == 1
    assert env.checkpoint_repo.delete_from_calls[0][0] == "session-1"
    assert len(env.session_repo.latest_message_calls) == 1


@pytest.mark.asyncio
async def test_restore_missing_anchor_event_raises_value_error():
    env = _Env(sessions=[_session()], checkpoints=[_checkpoint()])
    env.session_repo.anchor_seq = None

    with pytest.raises(ValueError, match="无法定位还原点"):
        await env.service.restore("session-1", "cp-1")


@pytest.mark.asyncio
async def test_restore_running_session_requests_cancel_and_ends_cancelled():
    env = _Env(
        sessions=[_session(status=SessionStatus.RUNNING, task_id="task-1")],
        checkpoints=[_checkpoint()],
    )

    await env.service.restore("session-1", "cp-1")

    assert env.task_state.cancel_calls == ["task-1"]
    assert env.session_repo.status_updates[-1] == ("session-1", SessionStatus.CANCELLED)


@pytest.mark.asyncio
async def test_restore_restores_sandbox_workspace_snapshot_when_key_present():
    sandbox = _FakeSandbox("sandbox-1")
    env = _Env(
        sessions=[_session(sandbox_id="sandbox-1")],
        checkpoints=[_checkpoint(sandbox_snapshot_key="checkpoints/session-1/cp-1.tgz")],
        sandbox_instances={"sandbox-1": sandbox},
    )
    env.object_storage.blobs["checkpoints/session-1/cp-1.tgz"] = b"workspace-bytes"

    await env.service.restore("session-1", "cp-1")

    assert len(sandbox.restore_workspace_calls) == 1
    assert sandbox.restore_workspace_calls[0][1] == b"workspace-bytes"


@pytest.mark.asyncio
async def test_restore_restores_browser_snapshot_when_key_present():
    sandbox = _FakeSandbox("sandbox-1")
    env = _Env(
        sessions=[_session(sandbox_id="sandbox-1")],
        checkpoints=[_checkpoint(browser_snapshot_key="checkpoints/session-1/cp-1_browser.tgz")],
        sandbox_instances={"sandbox-1": sandbox},
    )
    env.object_storage.blobs["checkpoints/session-1/cp-1_browser.tgz"] = b"browser-bytes"

    await env.service.restore("session-1", "cp-1")

    assert len(sandbox.restore_browser_calls) == 1
    assert sandbox.restore_browser_calls[0][1] == b"browser-bytes"


@pytest.mark.asyncio
async def test_restore_creates_sandbox_when_missing_but_snapshot_key_present():
    env = _Env(
        sessions=[_session(sandbox_id="gone-sandbox")],
        checkpoints=[_checkpoint(sandbox_snapshot_key="checkpoints/session-1/cp-1.tgz")],
        sandbox_instances={},
    )
    env.object_storage.blobs["checkpoints/session-1/cp-1.tgz"] = b"workspace-bytes"

    await env.service.restore("session-1", "cp-1")

    assert env.sandbox_cls.create_calls == 1
    assert env.session_repo.sessions["session-1"].sandbox_id == "sandbox-created"


@pytest.mark.asyncio
async def test_restore_recovers_latest_message_from_remaining_events():
    env = _Env(sessions=[_session()], checkpoints=[_checkpoint()])
    event = MessageEvent(role="user", message="hello again")
    env.session_repo.events_by_session["session-1"] = [(1, event)]

    await env.service.restore("session-1", "cp-1")

    session_id, message, _ts = env.session_repo.latest_message_calls[0]
    assert session_id == "session-1"
    assert message == "hello again"


# ---------------------------------------------------------------------------
# resume_latest_checkpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resume_latest_checkpoint_missing_session_returns_none():
    env = _Env(sessions=[], checkpoints=[])

    result = await env.service.resume_latest_checkpoint("session-1")

    assert result is None


@pytest.mark.asyncio
async def test_resume_latest_checkpoint_no_checkpoints_returns_none():
    env = _Env(sessions=[_session()], checkpoints=[])

    result = await env.service.resume_latest_checkpoint("session-1")

    assert result is None


@pytest.mark.asyncio
async def test_resume_latest_checkpoint_success_restores_snapshot_and_sets_running():
    checkpoint = _checkpoint()
    env = _Env(sessions=[_session(status=SessionStatus.FAILED)], checkpoints=[checkpoint])

    result = await env.service.resume_latest_checkpoint("session-1")

    assert result is not None
    assert result.id == "cp-1"
    assert env.session_repo.restore_snapshot_calls[0]["status"] == SessionStatus.RUNNING.value
    # Unlike restore(), events are never deleted — this is an at-least-once
    # replay boundary, not an undo.
    assert env.session_repo.delete_events_calls == []


@pytest.mark.asyncio
async def test_resume_latest_checkpoint_picks_the_last_checkpoint_in_session_order():
    cp1 = _checkpoint(id="cp-1")
    cp2 = _checkpoint(id="cp-2")
    env = _Env(sessions=[_session()], checkpoints=[cp1, cp2])

    result = await env.service.resume_latest_checkpoint("session-1")

    assert result.id == "cp-2"


@pytest.mark.asyncio
async def test_resume_latest_checkpoint_restores_sandbox_snapshot_when_key_present():
    sandbox = _FakeSandbox("sandbox-1")
    env = _Env(
        sessions=[_session(sandbox_id="sandbox-1")],
        checkpoints=[_checkpoint(sandbox_snapshot_key="checkpoints/session-1/cp-1.tgz")],
        sandbox_instances={"sandbox-1": sandbox},
    )
    env.object_storage.blobs["checkpoints/session-1/cp-1.tgz"] = b"workspace-bytes"

    await env.service.resume_latest_checkpoint("session-1")

    assert len(sandbox.restore_workspace_calls) == 1
