#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.domain.models.tool_approval import (
    ApprovalCallInput,
    ApprovalStatus,
    ToolApprovalBatch,
)
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.interfaces.endpoints.session import approval_routes


class _GovernanceRepository:
    def __init__(self, batch):
        self.batch = batch
        self.decisions = []
        self.decision_attempts = []

    async def get_pending_approval_batch(self, session_id):
        if self.batch.session_id != session_id:
            return None
        if self.batch.status != ApprovalStatus.PENDING:
            return None
        return self.batch

    async def decide_approval_call(self, tool_call_id, status, decided_by):
        self.decision_attempts.append((tool_call_id, status, decided_by))
        calls = list(self.batch.calls)
        call_index = next(
            index
            for index, call in enumerate(calls)
            if call.tool_call_id == tool_call_id
        )
        if calls[call_index].status != ApprovalStatus.PENDING:
            if calls[call_index].status == status:
                return calls[call_index]
            raise ValueError("approval call is already decided")
        calls[call_index] = calls[call_index].model_copy(
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
        self.decisions.append((tool_call_id, status, decided_by))
        return calls[call_index]


class _SessionRepository:
    def __init__(self):
        self.session = SimpleNamespace(id="s1", pending_metadata={})

    async def get_by_id(self, session_id, scope=None):
        if session_id != "s1":
            return None
        return self.session

    async def set_pending_metadata(self, session_id, metadata):
        self.session.pending_metadata = metadata


class _Uow:
    def __init__(self, batch):
        self.resource_governance = _GovernanceRepository(batch)
        self.session = _SessionRepository()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def approval_batch():
    return ToolApprovalBatch.for_calls(
        "s1",
        [
            ApprovalCallInput("tc1", "write_file", {"path": "one"}, 0),
            ApprovalCallInput(
                "tc2",
                "browser_click",
                {"target": "submit"},
                1,
            ),
        ],
    )


@pytest.fixture
def ctx():
    return WorkspaceContext(
        principal=Principal(user_id="u1"),
        scope=OwnerScope.personal("u1"),
    )


@pytest.mark.asyncio
async def test_pending_approval_route_returns_complete_ordered_batch(
    monkeypatch,
    approval_batch,
    ctx,
):
    uow = _Uow(approval_batch)
    monkeypatch.setattr(approval_routes, "get_uow", lambda: uow)

    response = await approval_routes.get_pending_tool_approval_batch(
        "s1",
        ctx,
    )

    assert response.data["id"] == approval_batch.id
    assert [
        call["tool_call_id"] for call in response.data["calls"]
    ] == ["tc1", "tc2"]
    assert response.data["calls"][0]["normalized_args"] == {"path": "one"}


@pytest.mark.asyncio
async def test_batch_decision_route_approves_every_pending_call_in_order(
    monkeypatch,
    approval_batch,
    ctx,
):
    uow = _Uow(approval_batch)
    monkeypatch.setattr(approval_routes, "get_uow", lambda: uow)

    response = await approval_routes.decide_tool_approval_batch(
        "s1",
        approval_batch.id,
        {"action": "approve"},
        ctx,
        ctx.principal,
    )

    assert response.data["status"] == ApprovalStatus.APPROVED.value
    assert uow.resource_governance.decisions == [
        ("tc1", ApprovalStatus.APPROVED, "u1"),
        ("tc2", ApprovalStatus.APPROVED, "u1"),
    ]


@pytest.mark.asyncio
async def test_batch_route_approve_same_updates_session_tool_allowance(
    monkeypatch,
    approval_batch,
    ctx,
):
    uow = _Uow(approval_batch)
    monkeypatch.setattr(approval_routes, "get_uow", lambda: uow)

    await approval_routes.decide_tool_approval_batch(
        "s1",
        approval_batch.id,
        {"action": "approve_same", "tool_call_ids": ["tc2"]},
        ctx,
        ctx.principal,
    )

    assert uow.session.session.pending_metadata["approved_tools"] == [
        "browser_click"
    ]


@pytest.mark.asyncio
async def test_generic_approve_does_not_expand_an_explicit_partial_decision(
    monkeypatch,
    approval_batch,
    ctx,
):
    uow = _Uow(approval_batch)
    monkeypatch.setattr(approval_routes, "get_uow", lambda: uow)
    await approval_routes.decide_tool_approval_batch(
        "s1",
        approval_batch.id,
        {"action": "approve", "tool_call_ids": ["tc1"]},
        ctx,
        ctx.principal,
    )

    response = await approval_routes.decide_tool_approval_batch(
        "s1",
        approval_batch.id,
        {"action": "approve"},
        ctx,
        ctx.principal,
    )

    assert response.data["status"] == ApprovalStatus.PENDING.value
    assert uow.resource_governance.decisions == [
        ("tc1", ApprovalStatus.APPROVED, "u1")
    ]
    assert uow.resource_governance.batch.calls[1].status == ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_explicit_identical_retry_preserves_original_decision_metadata(
    monkeypatch,
    approval_batch,
    ctx,
):
    uow = _Uow(approval_batch)
    monkeypatch.setattr(approval_routes, "get_uow", lambda: uow)
    first_response = await approval_routes.decide_tool_approval_batch(
        "s1",
        approval_batch.id,
        {"action": "approve", "tool_call_ids": ["tc1"]},
        ctx,
        ctx.principal,
    )
    original = first_response.data["calls"][0]

    response = await approval_routes.decide_tool_approval_batch(
        "s1",
        approval_batch.id,
        {"action": "approve", "tool_call_ids": ["tc1"]},
        ctx,
        ctx.principal,
    )

    retried = response.data["calls"][0]
    assert uow.resource_governance.decision_attempts == [
        ("tc1", ApprovalStatus.APPROVED, "u1"),
        ("tc1", ApprovalStatus.APPROVED, "u1"),
    ]
    assert retried["decided_by"] == original["decided_by"]
    assert retried["decided_at"] == original["decided_at"]


@pytest.mark.asyncio
async def test_explicit_conflicting_retry_surfaces_immutable_decision_conflict(
    monkeypatch,
    approval_batch,
    ctx,
):
    uow = _Uow(approval_batch)
    monkeypatch.setattr(approval_routes, "get_uow", lambda: uow)
    await approval_routes.decide_tool_approval_batch(
        "s1",
        approval_batch.id,
        {"action": "approve", "tool_call_ids": ["tc1"]},
        ctx,
        ctx.principal,
    )

    with pytest.raises(ValueError, match="already decided"):
        await approval_routes.decide_tool_approval_batch(
            "s1",
            approval_batch.id,
            {"action": "reject", "tool_call_ids": ["tc1"]},
            ctx,
            ctx.principal,
        )

    assert uow.resource_governance.decision_attempts[-1] == (
        "tc1",
        ApprovalStatus.REJECTED,
        "u1",
    )
    assert uow.resource_governance.batch.calls[0].status == (
        ApprovalStatus.APPROVED
    )


@pytest.mark.asyncio
async def test_explicit_approve_same_retry_does_not_grant_new_allowance(
    monkeypatch,
    approval_batch,
    ctx,
):
    uow = _Uow(approval_batch)
    monkeypatch.setattr(approval_routes, "get_uow", lambda: uow)
    await approval_routes.decide_tool_approval_batch(
        "s1",
        approval_batch.id,
        {"action": "approve", "tool_call_ids": ["tc1"]},
        ctx,
        ctx.principal,
    )

    await approval_routes.decide_tool_approval_batch(
        "s1",
        approval_batch.id,
        {"action": "approve_same", "tool_call_ids": ["tc1"]},
        ctx,
        ctx.principal,
    )

    assert uow.resource_governance.decision_attempts[-1] == (
        "tc1",
        ApprovalStatus.APPROVED,
        "u1",
    )
    assert uow.session.session.pending_metadata["approved_tools"] == []


@pytest.mark.asyncio
async def test_whole_batch_reject_only_decides_pending_mixed_batch_calls(
    monkeypatch,
    approval_batch,
    ctx,
):
    now = datetime.now(timezone.utc)
    policy_call = approval_batch.calls[0].model_copy(
        update={
            "status": ApprovalStatus.APPROVED,
            "decided_by": "policy",
            "decided_at": now,
        }
    )
    mixed_batch = approval_batch.model_copy(
        update={"calls": [policy_call, approval_batch.calls[1]]}
    )
    uow = _Uow(mixed_batch)
    monkeypatch.setattr(approval_routes, "get_uow", lambda: uow)

    response = await approval_routes.decide_tool_approval_batch(
        "s1",
        mixed_batch.id,
        {"action": "reject"},
        ctx,
        ctx.principal,
    )

    assert response.data["status"] == ApprovalStatus.REJECTED.value
    assert uow.resource_governance.decisions == [
        ("tc2", ApprovalStatus.REJECTED, "u1")
    ]
    assert uow.resource_governance.batch.calls[0].decided_by == "policy"


@pytest.mark.asyncio
async def test_approve_same_only_allows_calls_explicitly_approved_by_action(
    monkeypatch,
    approval_batch,
    ctx,
):
    now = datetime.now(timezone.utc)
    policy_call = approval_batch.calls[0].model_copy(
        update={
            "status": ApprovalStatus.APPROVED,
            "decided_by": "policy",
            "decided_at": now,
        }
    )
    mixed_batch = approval_batch.model_copy(
        update={"calls": [policy_call, approval_batch.calls[1]]}
    )
    uow = _Uow(mixed_batch)
    monkeypatch.setattr(approval_routes, "get_uow", lambda: uow)

    await approval_routes.decide_tool_approval_batch(
        "s1",
        mixed_batch.id,
        {"action": "approve_same"},
        ctx,
        ctx.principal,
    )

    assert uow.session.session.pending_metadata["approved_tools"] == [
        "browser_click"
    ]
