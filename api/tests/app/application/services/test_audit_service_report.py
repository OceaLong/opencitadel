import pytest

from app.application.ports.reporting import AuditVerificationKeyring
from app.application.services.audit_service import AuditService
from app.domain.models.audit_log import AuditLog
from tests.app.application_test_support import EmptyAuditSummaryQuery, NoopGovernanceMetrics


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
async def test_build_session_audit_report_json_is_a_generic_audit_timeline():
    repo = _FakeAuditRepo()
    service = AuditService(
        lambda: _FakeUow(repo),
        AuditVerificationKeyring(keys={}),
        NoopGovernanceMetrics(),
        EmptyAuditSummaryQuery(),
    )
    repo.items = [
        AuditLog(
            action="operator_scope_declared", resource_id="s1", metadata={"ownership": "owned"}
        ),
        AuditLog(action="session_updated", resource_id="s1", metadata={"field": "title"}),
    ]

    async def list_logs(**kwargs):
        return repo.items

    service.list_logs = list_logs  # type: ignore[method-assign]
    payload = await service.build_session_audit_report_json("s1")
    assert payload["session_id"] == "s1"
    assert [entry["action"] for entry in payload["entries"]] == [
        "operator_scope_declared",
        "session_updated",
    ]

    md = await service.build_session_audit_report("s1")
    assert "审计条目" in md
