#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unit tests for the 13+1 compliance evaluators that used to be
unconditional/near-unconditional passes (Task 4: 合规评估器真化).

Each evaluator is a ``staticmethod`` of ``ComplianceService`` with signature
``(metrics: dict, chain: dict) -> tuple[status, evidence]``. These tests call
the staticmethods directly with hand-built metrics dicts -- no DB, no uow --
so they pin down the *decision logic* independent of how the inputs are
collected (that wiring is covered separately by
``test_compliance_report.py``'s ``_collect_metrics`` monkeypatch smoke test).
"""
from datetime import datetime, timedelta, timezone

from app.application.services.compliance_service import ComplianceService


def _chain(ok: bool = True, total: int = 5) -> dict:
    return {"ok": ok, "total": total, "first_broken_seq": None}


class TestAuthPresent:
    def test_pass_when_login_actions_recorded(self):
        status, evidence = ComplianceService._eval_auth_present({"auth_event_count": 3}, {})
        assert status == "pass"
        assert any("3" in e for e in evidence)

    def test_attention_when_no_login_actions(self):
        status, evidence = ComplianceService._eval_auth_present({"auth_event_count": 0}, {})
        assert status == "attention"
        assert any("0" in e for e in evidence)


class TestRbacPresent:
    def test_pass_when_admin_and_non_admin_present(self):
        status, _ = ComplianceService._eval_rbac_present(
            {"role_distribution": {"admin": 1, "user": 5}}, {}
        )
        assert status == "pass"

    def test_attention_when_everyone_is_admin(self):
        status, _ = ComplianceService._eval_rbac_present(
            {"role_distribution": {"admin": 3}}, {}
        )
        assert status == "attention"

    def test_attention_when_no_admin_at_all(self):
        status, _ = ComplianceService._eval_rbac_present(
            {"role_distribution": {"user": 3}}, {}
        )
        assert status == "attention"


class TestGateApprovals:
    def test_pass_when_approvals_recorded(self):
        status, _ = ComplianceService._eval_gate_approvals(
            {"gate_approval_count": 2, "agent_session_count": 5}, {}
        )
        assert status == "pass"

    def test_attention_when_sessions_but_no_approvals(self):
        status, _ = ComplianceService._eval_gate_approvals(
            {"gate_approval_count": 0, "agent_session_count": 4}, {}
        )
        assert status == "attention"

    def test_not_verified_when_no_sessions_at_all(self):
        status, _ = ComplianceService._eval_gate_approvals(
            {"gate_approval_count": 0, "agent_session_count": 0}, {}
        )
        assert status == "not_verified"


class TestToolAudit:
    def test_pass_when_tool_invocations_recorded(self):
        status, _ = ComplianceService._eval_tool_audit(
            {"tool_invoke_count": 10, "agent_session_count": 3}, {}
        )
        assert status == "pass"

    def test_attention_when_sessions_but_no_tool_invocations(self):
        status, _ = ComplianceService._eval_tool_audit(
            {"tool_invoke_count": 0, "agent_session_count": 2}, {}
        )
        assert status == "attention"

    def test_not_verified_when_no_sessions_at_all(self):
        status, _ = ComplianceService._eval_tool_audit(
            {"tool_invoke_count": 0, "agent_session_count": 0}, {}
        )
        assert status == "not_verified"


class TestRedactionOn:
    def test_pass_when_sample_is_clean(self):
        status, _ = ComplianceService._eval_redaction_on(
            {"redaction_spot_check": [{"clean": True}, {"clean": True}]}, {}
        )
        assert status == "pass"

    def test_attention_when_sample_has_dirty_entry(self):
        status, evidence = ComplianceService._eval_redaction_on(
            {"redaction_spot_check": [{"clean": True}, {"clean": False}]}, {}
        )
        assert status == "attention"
        assert any("1" in e for e in evidence)

    def test_attention_when_no_sample_available(self):
        status, _ = ComplianceService._eval_redaction_on({"redaction_spot_check": []}, {})
        assert status == "attention"


class TestRollbackCapable:
    def test_pass_when_checkpoints_created_in_window(self):
        status, _ = ComplianceService._eval_rollback_capable({"checkpoint_count": 2}, {})
        assert status == "pass"

    def test_attention_when_no_checkpoints_in_window(self):
        status, _ = ComplianceService._eval_rollback_capable({"checkpoint_count": 0}, {})
        assert status == "attention"


class TestSelfHosted:
    def test_pass_when_all_hosts_private(self):
        status, _ = ComplianceService._eval_self_hosted(
            {"llm_endpoint_hosts": ["llm.internal.corp", "10.0.0.5"]}, {}
        )
        assert status == "pass"

    def test_attention_when_public_saas_host_present(self):
        status, evidence = ComplianceService._eval_self_hosted(
            {"llm_endpoint_hosts": ["llm.internal.corp", "api.openai.com"]}, {}
        )
        assert status == "attention"
        assert any("api.openai.com" in e for e in evidence)

    def test_not_verified_when_no_endpoints_configured(self):
        status, _ = ComplianceService._eval_self_hosted({"llm_endpoint_hosts": []}, {})
        assert status == "not_verified"

    def test_attention_for_azure_openai_resource_subdomain(self):
        # Azure OpenAI hostnames are per-resource: <resource>.openai.azure.com
        status, evidence = ComplianceService._eval_self_hosted(
            {"llm_endpoint_hosts": ["my-resource.openai.azure.com"]}, {}
        )
        assert status == "attention"
        assert any("openai.azure.com" in e for e in evidence)

    def test_attention_for_bedrock_regional_subdomain(self):
        status, _ = ComplianceService._eval_self_hosted(
            {"llm_endpoint_hosts": ["bedrock-runtime.us-east-1.amazonaws.com"]}, {}
        )
        assert status == "attention"

    def test_pass_for_unrelated_amazonaws_host(self):
        # Same TLD/base domain as Bedrock but no "bedrock" keyword -- must
        # not be flagged (S3/other AWS services aren't LLM SaaS).
        status, _ = ComplianceService._eval_self_hosted(
            {"llm_endpoint_hosts": ["my-bucket.s3.amazonaws.com"]}, {}
        )
        assert status == "pass"

    def test_attention_for_vertex_aiplatform_regional_subdomain(self):
        status, _ = ComplianceService._eval_self_hosted(
            {"llm_endpoint_hosts": ["us-central1-aiplatform.googleapis.com"]}, {}
        )
        assert status == "attention"

    def test_attention_for_cn_providers(self):
        for host in ("dashscope.aliyuncs.com", "api.deepseek.com", "open.bigmodel.cn", "api.moonshot.cn"):
            status, _ = ComplianceService._eval_self_hosted({"llm_endpoint_hosts": [host]}, {})
            assert status == "attention", host


class TestEvidenceExport:
    def test_pass_when_export_actions_recorded(self):
        status, _ = ComplianceService._eval_evidence_export({"evidence_export_count": 1}, {})
        assert status == "pass"

    def test_attention_when_no_export_actions(self):
        status, _ = ComplianceService._eval_evidence_export({"evidence_export_count": 0}, {})
        assert status == "attention"


class TestSessionIsolation:
    def test_pass_when_driver_configured(self):
        status, _ = ComplianceService._eval_session_isolation({"sandbox_driver": "docker"}, {})
        assert status == "pass"

    def test_gap_when_driver_empty(self):
        status, _ = ComplianceService._eval_session_isolation({"sandbox_driver": ""}, {})
        assert status == "gap"

    def test_gap_when_driver_missing(self):
        status, _ = ComplianceService._eval_session_isolation({}, {})
        assert status == "gap"


class TestEncryptionAtRest:
    def test_always_not_verified(self):
        status, evidence = ComplianceService._eval_encryption_at_rest({}, {})
        assert status == "not_verified"
        assert any("磁盘加密" in e for e in evidence)


class TestCentralAdmin:
    def test_pass_when_admin_actions_recorded(self):
        status, _ = ComplianceService._eval_central_admin({"admin_action_count": 5}, {})
        assert status == "pass"

    def test_attention_when_no_admin_actions(self):
        status, _ = ComplianceService._eval_central_admin({"admin_action_count": 0}, {})
        assert status == "attention"


class TestTimestampIntegrity:
    """``timestamp_chain_sample`` is what AuditRepository.list_recent_chained
    actually returns: entries ordered *ascending by chain_seq* (the
    write-order hash-chain sequence), independent of created_at. Tests build
    samples in that shape -- chain_seq strictly increasing by list position,
    created_at set (or deliberately not set) to match/mismatch that order --
    instead of a hand-sorted-by-created_at list, so a failing test here
    would actually mean the evaluator's monotonicity check is broken, not
    that the fixture merely disagrees with a re-sort the evaluator no
    longer performs.

    In the real deployment, ``AuditLogORM.created_at`` is a
    ``DateTime`` column *without* ``timezone=True``, so every row read back
    from the DB is a naive ``datetime`` -- ``all_aware`` would be
    unconditionally False and the evaluator would report a permanent
    ``gap`` regardless of actual data integrity. The primary judgment is
    therefore chain-seq-order monotonicity of created_at (valid and
    comparable whether naive or aware); tz-awareness is a secondary,
    best-effort signal that degrades to ``attention`` (not ``gap``) for the
    naive case that is the real, expected shape of production data.
    """

    def test_not_verified_when_sample_empty(self):
        status, _ = ComplianceService._eval_timestamp_integrity(
            {"timestamp_chain_sample": []}, {}
        )
        assert status == "not_verified"

    def test_pass_when_all_aware_and_created_at_tracks_chain_seq_order(self):
        base = datetime(2026, 8, 1, tzinfo=timezone.utc)
        # chain_seq 101, 102, 103 (ascending, as list_recent_chained
        # returns) with created_at increasing in lockstep -- the expected
        # steady-state shape when the schema is tz-aware.
        sample = [
            {"chain_seq": 101, "created_at": base},
            {"chain_seq": 102, "created_at": base + timedelta(minutes=1)},
            {"chain_seq": 103, "created_at": base + timedelta(minutes=2)},
        ]
        status, _ = ComplianceService._eval_timestamp_integrity(
            {"timestamp_chain_sample": sample}, {}
        )
        assert status == "pass"

    def test_attention_when_created_at_naive_and_monotonic(self):
        # Real production shape: AuditLogORM.created_at is DateTime without
        # timezone=True, so rows read back naive. Monotonic along chain_seq
        # order is still a real, checkable property -- this must not be a
        # permanent gap.
        sample = [
            {"chain_seq": 101, "created_at": datetime(2026, 8, 1, 0, 0)},
            {"chain_seq": 102, "created_at": datetime(2026, 8, 1, 0, 1)},
            {"chain_seq": 103, "created_at": datetime(2026, 8, 1, 0, 2)},
        ]
        status, evidence = ComplianceService._eval_timestamp_integrity(
            {"timestamp_chain_sample": sample}, {}
        )
        assert status == "attention"
        assert any("TIMESTAMP WITHOUT TIME ZONE" in e or "tz-aware" in e for e in evidence)

    def test_gap_when_created_at_goes_backwards_along_chain_seq(self):
        # naive datetimes -- the real pipeline shape -- with chain_seq
        # strictly ascending (101 < 102 < 103, guaranteed by DB write
        # order) but created_at at chain_seq=102 earlier than at
        # chain_seq=101 -- e.g. clock skew or a backdated write.
        base = datetime(2026, 8, 1)
        sample = [
            {"chain_seq": 101, "created_at": base + timedelta(minutes=5)},
            {"chain_seq": 102, "created_at": base},
            {"chain_seq": 103, "created_at": base + timedelta(minutes=10)},
        ]
        status, evidence = ComplianceService._eval_timestamp_integrity(
            {"timestamp_chain_sample": sample}, {}
        )
        assert status == "gap"
        assert any("链序" in e for e in evidence)


class TestMonitoringPresent:
    def test_pass_when_metrics_token_configured(self):
        status, _ = ComplianceService._eval_monitoring_present(
            {"metrics_token_configured": True}, {}
        )
        assert status == "pass"

    def test_attention_when_metrics_token_missing(self):
        status, evidence = ComplianceService._eval_monitoring_present(
            {"metrics_token_configured": False}, {}
        )
        assert status == "attention"
        assert any("metrics_token" in e for e in evidence)


class TestCryptoControls:
    def test_pass_when_chain_intact_and_key_rotated(self):
        status, _ = ComplianceService._eval_crypto_controls(
            {"signing_key_is_default": False, "audit_signing_key_id": "primary"},
            _chain(ok=True),
        )
        assert status == "pass"

    def test_attention_when_key_still_default(self):
        status, evidence = ComplianceService._eval_crypto_controls(
            {"signing_key_is_default": True, "audit_signing_key_id": "primary"},
            _chain(ok=True),
        )
        assert status == "attention"
        assert any("默认值" in e for e in evidence)

    def test_gap_when_chain_broken(self):
        status, _ = ComplianceService._eval_crypto_controls(
            {"signing_key_is_default": False}, _chain(ok=False, total=3)
        )
        assert status == "gap"
