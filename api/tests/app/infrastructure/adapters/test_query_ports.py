from __future__ import annotations

from pathlib import Path

from app.application.ports.queries import (
    AuditSummary,
    EvidenceSession,
    UsageSummary,
)


def test_query_dtos_are_persistence_agnostic() -> None:
    summary = UsageSummary(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        cached_tokens=2,
        call_count=1,
    )
    evidence = EvidenceSession(
        session_id="session-1",
        title="Session",
        owner_user_id="user-1",
        team_id=None,
        operator_scope=None,
        status="completed",
        updated_at=None,
    )
    audit = AuditSummary(by_day=(), by_action=())

    assert summary.total_tokens == 15
    assert evidence.owner_user_id == "user-1"
    assert audit.by_day == ()
    assert not hasattr(summary, "_sa_instance_state")
    assert not hasattr(evidence, "_sa_instance_state")


def test_target_application_services_have_no_persistence_imports() -> None:
    root = Path(__file__).resolve().parents[4] / "app/application/services"
    names = (
        "audit_service.py",
        "compliance_service.py",
        "evidence_service.py",
        "quota_service.py",
        "usage_stats_service.py",
        "patrol_retention_service.py",
        "governance_overview_service.py",
        "governance_profile_service.py",
        "agent_service.py",
        "codebase_service.py",
        "knowledge_base_service.py",
        "scheduled_job_service.py",
    )
    sources = "\n".join((root / name).read_text() for name in names)

    assert "app.infrastructure" not in sources
    assert "sqlalchemy" not in sources
