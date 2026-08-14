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
