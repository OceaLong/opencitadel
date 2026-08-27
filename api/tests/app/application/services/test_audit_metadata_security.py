import pytest

from app.application.ports.reporting import AuditVerificationKeyring
from app.application.services.audit_service import AuditService
from app.domain.models.audit_log import AuditLog
from tests.app.application_test_support import EmptyAuditSummaryQuery, NoopGovernanceMetrics


class _AuditRepo:
    def __init__(self):
        self.saved = None

    async def add(self, log):
        self.saved = log


class _Uow:
    def __init__(self, repo):
        self.audit = repo

    async def __aenter__(self):
        return self

    async def commit(self):
        return None

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_audit_service_redacts_nested_secret_metadata():
    repo = _AuditRepo()
    service = AuditService(
        lambda: _Uow(repo),
        AuditVerificationKeyring(keys={}),
        NoopGovernanceMetrics(),
        EmptyAuditSummaryQuery(),
    )

    await service.record(
        AuditLog(
            action="inference_endpoint_update",
            metadata={
                "api_key": "sk-do-not-store",
                "before": {
                    "provider": "openai",
                    "headers": {"Authorization": "Bearer secret"},
                },
                "password_changed": True,
            },
        )
    )

    assert repo.saved.metadata == {
        "api_key": "[REDACTED]",
        "before": {
            "provider": "openai",
            "headers": "[REDACTED]",
        },
        "password_changed": "[REDACTED]",
    }
