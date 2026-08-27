import io
import json
import zipfile
from datetime import UTC, datetime

import pytest

from app.application.services.evidence_service import (
    EvidenceService,
    render_governance_profile_md,
)
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session, SessionStatus
from tests.app.application_test_support import (
    EmptyEvidenceSessionQuery,
    FakeReportRenderer,
    FixedEvidenceSigner,
)


def _profile(*, feedback: str = "approved") -> dict:
    return {
        "session": {
            "id": "session-1",
            "title": "demo",
            "status": "completed",
            "operator_scope": "owned",
            "operator_domains": ["ops-console"],
            "created_at": "2026-08-24T00:00:00+00:00",
            "updated_at": "2026-08-24T00:01:00+00:00",
        },
        "chain": {"verified": True, "checked_runs": 1, "checked_entries": 7},
        "runs": [
            {
                "run_id": "run-1",
                "family": "agent",
                "status": "completed",
                "created_at": "2026-08-24T00:00:00+00:00",
                "terminal_at": "2026-08-24T00:01:00+00:00",
            }
        ],
        "approvals": [
            {
                "approval_id": "approval-1",
                "requested_at": "2026-08-24T00:00:10+00:00",
                "decided_at": "2026-08-24T00:00:20+00:00",
                "status": "approved",
                "decided_by_user_id": "user-1",
                "subject_label": "shell_execute",
                "feedback": feedback,
            }
        ],
        "activities": [
            {
                "activity_id": "activity-1",
                "activity_type": "tool.call",
                "status": "completed",
                "attempt": 1,
                "failure_code": None,
                "created_at": "2026-08-24T00:00:30+00:00",
            }
        ],
    }


class _SessionRepo:
    def __init__(self, session):
        self.value = session

    async def get_by_id(self, session_id, *, scope=None):
        return self.value if session_id == self.value.id else None


class _AuditRepo:
    async def list_chained(self, *, resource_id):
        return []


class _Uow:
    def __init__(self, session):
        self.session = _SessionRepo(session)
        self.audit = _AuditRepo()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _AuditService:
    async def verify_session_chain(self, session_id):
        return {"ok": True, "total": 0}

    async def build_session_audit_report_json(self, session_id):
        return {"session_id": session_id, "entries": []}

    async def build_session_audit_report(self, session_id):
        return f"# Audit {session_id}\n"


class _Artifacts:
    async def list_by_session(self, session_id, *, scope):
        return []


class _Governance:
    def __init__(self, profile):
        self.profile = profile
        self.calls = []

    async def build_profile(self, session_id, *, scope):
        self.calls.append((session_id, scope))
        return self.profile


@pytest.fixture
def service():
    session = Session(
        id="session-1",
        title="demo",
        owner_user_id="user-1",
        operator_scope="owned",
        operator_domains=["ops-console"],
        status=SessionStatus.COMPLETED,
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
        updated_at=datetime(2026, 8, 24, 0, 1, tzinfo=UTC),
    )
    governance = _Governance(_profile())
    return (
        EvidenceService(
            lambda: _Uow(session),
            _AuditService(),
            _Artifacts(),
            governance,
            FakeReportRenderer(None),
            FixedEvidenceSigner(),
            EmptyEvidenceSessionQuery(),
        ),
        governance,
    )


def test_governance_markdown_contains_only_formal_sections():
    rendered = render_governance_profile_md(_profile()).decode()

    assert "Run 时间线" in rendered
    assert "审批时间线" in rendered
    assert "Activity 时间线" in rendered
    assert "shell_execute" in rendered
    assert "checkpoint" not in rendered.lower()
    assert "gate profile" not in rendered.lower()


@pytest.mark.asyncio
async def test_evidence_package_contains_signed_formal_governance(service):
    evidence, governance = service
    scope = OwnerScope.personal("user-1")

    package = await evidence.build_session_evidence_package("session-1", scope)

    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = set(archive.namelist())
        assert {
            "audit.json",
            "audit-report.md",
            "governance-profile.json",
            "governance-profile.md",
            "manifest.json",
            "chain-signature.txt",
        } <= names
        manifest = json.loads(archive.read("manifest.json"))
        profile = json.loads(archive.read("governance-profile.json"))
        assert manifest["execution_chain_verification"]["verified"] is True
        assert manifest["pdf"] == "skipped"
        assert profile["runs"][0]["run_id"] == "run-1"
        assert "HMAC-SHA256" in archive.read("chain-signature.txt").decode()
    assert governance.calls == [("session-1", scope)]


@pytest.mark.asyncio
async def test_profile_export_scrubs_secrets():
    secret_profile = _profile(feedback="Authorization: Bearer super-secret-token")
    governance = _Governance(secret_profile)
    session = Session(
        id="session-1",
        title="demo",
        owner_user_id="user-1",
        status=SessionStatus.COMPLETED,
    )
    evidence = EvidenceService(
        lambda: _Uow(session),
        _AuditService(),
        _Artifacts(),
        governance,
        FakeReportRenderer(None),
        FixedEvidenceSigner(),
        EmptyEvidenceSessionQuery(),
    )

    package = await evidence.build_session_evidence_package(
        "session-1", OwnerScope.personal("user-1")
    )

    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        exported = archive.read("governance-profile.json").decode()
        markdown = archive.read("governance-profile.md").decode()
    assert "super-secret-token" not in exported
    assert "super-secret-token" not in markdown
