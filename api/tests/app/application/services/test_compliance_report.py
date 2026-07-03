#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.services.compliance_service import ComplianceService
from app.domain.services.compliance.control_mapping import CONTROLS


class _FakeAudit:
    async def verify_chain(self, **kwargs):
        return {"ok": True, "total": 0, "first_broken_seq": None, "checked_at": "2026-07-03T00:00:00Z"}


class _FakeSession:
    async def execute(self, stmt):
        class _R:
            def scalar_one(self):
                return 0

        return _R()


class _FakeUow:
    def __init__(self):
        self.db_session = _FakeSession()
        self.audit = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_compliance_report_includes_all_controls(monkeypatch):
    service = ComplianceService(lambda: _FakeUow(), _FakeAudit())

    async def _metrics(*args, **kwargs):
        return {
            "audit_count": 1,
            "gate_approval_count": 0,
            "tool_invoke_count": 0,
            "operator_scope_count": 0,
            "operator_sessions": 0,
            "rollback_count": 0,
            "hitl_enabled": True,
            "plan_gate": True,
            "tool_gate": True,
            "gate_profiles": ["standard"],
            "redaction_module": True,
            "self_hosted": True,
            "evidence_export": True,
            "session_isolation": True,
            "encryption_at_rest": True,
        }

    monkeypatch.setattr(service, "_collect_metrics", _metrics)
    report = await service.build_report()
    assert len(report["controls"]) == len(CONTROLS)
    assert report["summary"]["total"] == len(CONTROLS)
    for item in report["controls"]:
        assert item["status"] in ("pass", "gap", "na")

    md = service.render_markdown(report)
    assert "合规审计报告" in md
