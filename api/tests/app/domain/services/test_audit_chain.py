import logging
from datetime import UTC, datetime

import pytest
from prometheus_client import REGISTRY

from app.application.ports.reporting import AuditVerificationKeyring
from app.domain.models.audit_log import AuditLog
from app.domain.services.audit_chain import (
    GENESIS,
    compute_entry_hash,
    entry_fields,
)
from app.infrastructure.adapters.reporting_ports import PrometheusGovernanceMetricsAdapter
from tests.app.application_test_support import EmptyAuditSummaryQuery


def _counter_value(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_compute_entry_hash_deterministic():
    secret = "test-secret-key-at-least-32-chars!!"
    fields = entry_fields(
        chain_seq=1,
        id="log-1",
        actor_user_id="u1",
        actor_ip="127.0.0.1",
        action="resource_updated",
        resource_type="session",
        resource_id="s1",
        team_id=None,
        request_id="req-1",
        metadata={"resource": "session"},
        created_at=datetime(2026, 7, 3, 0, 0, 0, tzinfo=UTC),
    )
    h1 = compute_entry_hash(secret, fields, GENESIS)
    h2 = compute_entry_hash(secret, fields, GENESIS)
    assert h1 == h2
    assert len(h1) == 64


def test_session_correlation_is_part_of_the_signed_audit_entry():
    common = {
        "chain_seq": 1,
        "id": "log-1",
        "actor_user_id": "u1",
        "actor_ip": "127.0.0.1",
        "action": "patrol_run_triggered",
        "resource_type": "patrol_run",
        "resource_id": "run-1",
        "team_id": "team-1",
        "request_id": "req-1",
        "metadata": {},
        "created_at": datetime(2026, 7, 3, tzinfo=UTC),
    }

    first = entry_fields(**common, session_id="session-1")
    second = entry_fields(**common, session_id="session-2")

    assert first["session_id"] == "session-1"
    assert compute_entry_hash("secret", first, GENESIS) != compute_entry_hash(
        "secret", second, GENESIS
    )


def test_entry_fields_rejects_naive_created_at():
    with pytest.raises(ValueError, match="timezone-aware"):
        entry_fields(
            chain_seq=1,
            id="log-1",
            actor_user_id=None,
            actor_ip="",
            action="test",
            resource_type="session",
            resource_id="s1",
            team_id=None,
            request_id="req-1",
            metadata={},
            created_at=datetime(2026, 7, 3, tzinfo=UTC).replace(tzinfo=None),
        )


@pytest.mark.asyncio
async def test_verify_chain_detects_tamper_and_emits_critical_alert(caplog):
    from app.application.services.audit_service import AuditService

    secret = "test-secret-key-at-least-32-chars!!"

    class _Repo:
        async def list_chained(self, **kwargs):
            created = datetime(2026, 7, 3, 0, 0, 0, tzinfo=UTC)
            f1 = entry_fields(
                chain_seq=1,
                id="a",
                actor_user_id=None,
                actor_ip="",
                action="test",
                resource_type="",
                resource_id="",
                team_id=None,
                request_id="",
                metadata={},
                created_at=created,
            )
            h1 = compute_entry_hash(secret, f1, GENESIS)
            log1 = AuditLog(
                id="a",
                action="test",
                chain_seq=1,
                signing_key_id="primary",
                prev_hash=GENESIS,
                entry_hash=h1,
                created_at=created,
            )
            log2 = AuditLog(
                id="b",
                action="test",
                chain_seq=2,
                signing_key_id="primary",
                prev_hash=h1,
                entry_hash="bad" * 16,
                created_at=created,
            )
            return [log1, log2]

    class _Uow:
        def __init__(self):
            self.audit = _Repo()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    service = AuditService(
        lambda: _Uow(),
        AuditVerificationKeyring(keys={"primary": (secret,)}),
        PrometheusGovernanceMetricsAdapter(),
        EmptyAuditSummaryQuery(),
    )
    before = _counter_value("governance_audit_chain_verifications_total", {"result": "broken"})
    with caplog.at_level(logging.CRITICAL):
        result = await service.verify_chain()

    after = _counter_value("governance_audit_chain_verifications_total", {"result": "broken"})
    assert result["ok"] is False
    assert result["first_broken_seq"] == 2
    assert "AUDIT_CHAIN_INTEGRITY_FAILURE" in caplog.text
    assert after - before == 1.0


@pytest.mark.asyncio
async def test_session_verification_uses_global_chain_not_invalid_filtered_subset():
    from app.application.services.audit_service import AuditService

    secret = "test-secret-key-at-least-32-chars!!"
    created = datetime(2026, 7, 3, 0, 0, 0, tzinfo=UTC)
    logs = []
    previous = GENESIS
    for seq, resource_id in ((1, "other"), (2, "session-1"), (3, "other")):
        fields = entry_fields(
            chain_seq=seq,
            id=f"log-{seq}",
            actor_user_id=None,
            actor_ip="",
            action="test",
            resource_type="session",
            resource_id=resource_id,
            team_id=None,
            session_id=resource_id,
            request_id="",
            metadata={},
            created_at=created,
        )
        entry_hash = compute_entry_hash(secret, fields, previous)
        logs.append(
            AuditLog(
                id=f"log-{seq}",
                action="test",
                resource_type="session",
                resource_id=resource_id,
                session_id=resource_id,
                chain_seq=seq,
                signing_key_id="primary",
                prev_hash=previous,
                entry_hash=entry_hash,
                created_at=created,
            )
        )
        previous = entry_hash

    class _Repo:
        async def list_chained(self, *, session_id=None, **kwargs):
            if session_id:
                return [log for log in logs if log.session_id == session_id]
            return logs

    class _Uow:
        audit = _Repo()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    before = _counter_value("governance_audit_chain_verifications_total", {"result": "intact"})

    result = await AuditService(
        lambda: _Uow(),
        AuditVerificationKeyring(keys={"primary": (secret,)}),
        PrometheusGovernanceMetricsAdapter(),
        EmptyAuditSummaryQuery(),
    ).verify_session_chain("session-1")

    after = _counter_value("governance_audit_chain_verifications_total", {"result": "intact"})
    assert result["ok"] is True
    assert result["session_ok"] is True
    assert result["session_entries"] == 1
    # verify_session_chain delegates its actual verification to verify_chain()
    # exactly once — it must not double-record the outcome.
    assert after - before == 1.0
