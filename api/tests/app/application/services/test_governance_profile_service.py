#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GovernanceProfileService: per-session governance profile read-model.

Fixture naming follows the task-2 brief (`gov_env`); the fake uow/repo
shapes mirror tests/app/application/services/test_patrol_run_service.py
and test_compliance_report.py (fake repo + fake collaborators, no real DB).
"""
import pytest

from app.application.errors.exceptions import NotFoundError
from app.application.services.governance_profile_service import (
    GovernanceProfileService,
)
from app.domain.models.audit_log import AuditLog
from app.domain.models.checkpoint import Checkpoint, CheckpointAnchorType
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.models.session import Session, SessionStatus


class _FakeSessionRepo:
    def __init__(self):
        self.items: dict[str, Session] = {}

    async def get_by_id(self, session_id, scope=None):
        session = self.items.get(session_id)
        if session is None:
            return None
        if scope is not None:
            if scope.type == OwnerScopeType.TEAM:
                if session.team_id != scope.team_id:
                    return None
            else:
                if session.owner_user_id != scope.user_id or session.team_id is not None:
                    return None
        return session


class _FakeAuditRepo:
    def __init__(self):
        self.items: list[AuditLog] = []
        self._seq = 0

    async def add(self, log: AuditLog):
        self._seq += 1
        log.chain_seq = self._seq
        self.items.append(log)

    async def list_chained(self, *, limit=None, resource_id=None):
        items = [
            log for log in self.items
            if resource_id is None or log.resource_id == resource_id
        ]
        items = sorted(items, key=lambda log: log.chain_seq)
        if limit is not None:
            items = items[:limit]
        return items


class _FakeCheckpointRepo:
    def __init__(self):
        self.items: list[Checkpoint] = []

    async def list_by_session(self, session_id):
        return [cp for cp in self.items if cp.session_id == session_id]


class _FakeUow:
    def __init__(self, session_repo, audit_repo, checkpoint_repo):
        self.session = session_repo
        self.audit = audit_repo
        self.checkpoint = checkpoint_repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeAuditService:
    """Stubs verify_session_chain; real hash-chain crypto is covered by
    tests/app/application/services/test_audit_service*.py, not here."""

    def __init__(self, audit_repo):
        self._audit_repo = audit_repo

    async def verify_session_chain(self, session_id):
        entries = await self._audit_repo.list_chained(resource_id=session_id)
        return {
            "ok": True,
            "total": len(self._audit_repo.items),
            "first_broken_seq": None,
            "checked_at": "2026-08-04T00:00:00Z",
            "session_id": session_id,
            "session_entries": len(entries),
            "session_ok": True,
            "session_first_broken_seq": None,
        }


class _FakeCheckpointService:
    def __init__(self, checkpoint_repo):
        self._repo = checkpoint_repo

    async def list_checkpoints(self, session_id):
        return await self._repo.list_by_session(session_id)


class _GovEnv:
    def __init__(self):
        self.sessions = _FakeSessionRepo()
        self.audit_repo = _FakeAuditRepo()
        self.checkpoints = _FakeCheckpointRepo()
        self._uow = _FakeUow(self.sessions, self.audit_repo, self.checkpoints)
        self.audit_service = _FakeAuditService(self.audit_repo)
        self.checkpoint_service = _FakeCheckpointService(self.checkpoints)
        self.service = GovernanceProfileService(
            lambda: self._uow, self.audit_service, self.checkpoint_service
        )
        self.scope = OwnerScope.personal("user-1")
        self._counter = 0

    async def create_session(self, *, status="pending", gate_profile=None):
        self._counter += 1
        sid = f"session-{self._counter}"
        self.sessions.items[sid] = Session(
            id=sid,
            title=f"session {self._counter}",
            owner_user_id="user-1",
            team_id=None,
            gate_profile=gate_profile,
            operator_scope="owned",
            status=SessionStatus(status),
        )
        return sid

    async def create_session_in_other_team(self):
        self._counter += 1
        sid = f"session-foreign-{self._counter}"
        self.sessions.items[sid] = Session(
            id=sid,
            title="foreign session",
            owner_user_id="user-2",
            team_id="team-x",
            status=SessionStatus.PENDING,
        )
        return sid

    async def write_audit(self, session_id, *, action, metadata):
        await self.audit_repo.add(
            AuditLog(
                actor_user_id="user-1",
                action=action,
                resource_type="session",
                resource_id=session_id,
                metadata=metadata,
            )
        )

    async def create_checkpoint(self, session_id, *, label):
        self.checkpoints.items.append(
            Checkpoint(
                id=f"cp-{len(self.checkpoints.items) + 1}",
                session_id=session_id,
                anchor_type=CheckpointAnchorType.STEP,
                anchor_event_id="evt-1",
                label=label,
            )
        )


@pytest.fixture
def gov_env():
    return _GovEnv()


@pytest.mark.asyncio
async def test_profile_aggregates_approvals_gates_checkpoints_terminal(gov_env):
    """审批/拒绝/gate 命中/检查点/终态在档案中各归其位。"""
    sid = await gov_env.create_session(status="failed", gate_profile="strict")
    await gov_env.write_audit(
        sid,
        action="agent_tool_approve",
        metadata={
            "decision": "approve",
            "tool": "browser_click",
            "pending_phase": "TOOL_APPROVAL_PHASE",
            "approval_batch_id": "b1",
        },
    )
    await gov_env.write_audit(
        sid,
        action="agent_tool_invoke",
        metadata={"tool": "shell_exec", "gated": True, "gate_profile": "strict"},
    )
    await gov_env.create_checkpoint(sid, label="before-shell")

    profile = await gov_env.service.build_profile(sid, scope=gov_env.scope)

    assert profile["session"]["id"] == sid
    assert profile["terminal"]["status"] == "failed"
    assert [a["action"] for a in profile["approvals"]] == ["agent_tool_approve"]
    assert profile["gate_hits"][0]["tool"] == "shell_exec"
    assert profile["gate_hits"][0]["gated"] is True
    assert len(profile["checkpoints"]) == 1
    assert profile["chain"]["verified"] is True


@pytest.mark.asyncio
async def test_profile_scope_mismatch_returns_not_found(gov_env):
    """跨租户 session 返回 NotFound，不泄露存在性（对齐 Patrol 模式）。"""
    sid = await gov_env.create_session_in_other_team()
    with pytest.raises(NotFoundError):
        await gov_env.service.build_profile(sid, scope=gov_env.scope)


@pytest.mark.asyncio
async def test_gate_hit_without_approval_is_not_an_approval(gov_env):
    """gated 但策略自动放行的调用只进 gate_hits，不进 approvals。"""
    sid = await gov_env.create_session(status="completed")
    await gov_env.write_audit(
        sid,
        action="agent_tool_invoke",
        metadata={"tool": "file_write", "gated": True, "gate_profile": "balanced"},
    )

    profile = await gov_env.service.build_profile(sid, scope=gov_env.scope)

    assert profile["approvals"] == []
    assert len(profile["gate_hits"]) == 1


@pytest.mark.asyncio
async def test_profile_surfaces_policy_denials_as_their_own_section(gov_env):
    """Task 2: agent_tool_denied audit rows get their own `denials` key —
    distinct from approvals/gate_hits, which only cover the HITL approval
    and gate-policy paths, not outright capability-policy rejections."""
    sid = await gov_env.create_session(status="failed", gate_profile="strict")
    await gov_env.write_audit(
        sid,
        action="agent_tool_denied",
        metadata={
            "tool": "write_file",
            "layer": "execution",
            "reason": "当前会话策略禁止工具[write_file]",
            "gate_profile": "strict",
        },
    )
    await gov_env.write_audit(
        sid,
        action="agent_tool_invoke",
        metadata={"tool": "shell_exec", "gated": True, "gate_profile": "strict"},
    )

    profile = await gov_env.service.build_profile(sid, scope=gov_env.scope)

    assert len(profile["denials"]) == 1
    denial = profile["denials"][0]
    assert denial["tool"] == "write_file"
    assert denial["layer"] == "execution"
    assert denial["reason"] == "当前会话策略禁止工具[write_file]"
    assert "created_at" in denial
    # A denial is not a gate hit or an approval — it must not leak into
    # either of those sections (the unrelated gated shell_exec invoke is
    # still exactly one gate hit, proving the sections stay independent).
    assert len(profile["gate_hits"]) == 1
    assert profile["approvals"] == []


@pytest.mark.asyncio
async def test_profile_denials_defaults_to_empty_list(gov_env):
    sid = await gov_env.create_session(status="completed")

    profile = await gov_env.service.build_profile(sid, scope=gov_env.scope)

    assert profile["denials"] == []
