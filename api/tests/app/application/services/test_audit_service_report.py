#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.services.audit_service import AuditService
from app.domain.models.audit_log import AuditLog


class _FakeAuditRepo:
    def __init__(self):
        self.items = []

    async def add(self, log: AuditLog):
        self.items.append(log)


class _FakeUow:
    def __init__(self, repo):
        self.audit = repo
        self.db_session = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_build_session_audit_report_json_partitions():
    repo = _FakeAuditRepo()
    service = AuditService(lambda: _FakeUow(repo))
    repo.items = [
        AuditLog(action="operator_scope_declared", resource_id="s1", metadata={"ownership": "owned"}),
        AuditLog(
            action="agent_tool_invoke",
            resource_id="s1",
            metadata={"tool": "browser_click", "success": True, "duration_ms": 12},
        ),
    ]

    async def list_logs(**kwargs):
        return repo.items

    service.list_logs = list_logs  # type: ignore[method-assign]
    payload = await service.build_session_audit_report_json("s1")
    assert payload["session_id"] == "s1"
    assert len(payload["governance_actions"]) == 1
    assert len(payload["tool_invocations"]) == 1
    assert payload["tool_invocations"][0]["tool"] == "browser_click"

    md = await service.build_session_audit_report("s1")
    assert "治理动作" in md
    assert "工具调用明细" in md
