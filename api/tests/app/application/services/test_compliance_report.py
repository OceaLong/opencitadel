import pytest

from app.application.ports.queries import ComplianceEvidenceSnapshot
from app.application.ports.reporting import ComplianceRuntimeValues
from app.application.services.compliance_service import ComplianceService
from app.domain.services.compliance.control_mapping import CONTROLS
from tests.app.application_test_support import FakeReportRenderer
from tests.runtime_policy_support import MutablePolicyReader


class _FakeAudit:
    async def verify_chain(self, **kwargs):
        return {
            "ok": True,
            "total": 0,
            "first_broken_seq": None,
            "checked_at": "2026-07-03T00:00:00Z",
        }


class _FakeSession:
    async def execute(self, stmt):
        class _R:
            def scalar_one(self):
                return 0

        return _R()


class _FakeAuditRepo:
    """Minimal in-memory stand-in for AuditRepository -- every method
    _collect_metrics actually calls, each returning an empty/zero result
    (real production shape when the window has no data)."""

    async def count_by_actions(self, actions, *, start_at=None, end_at=None):
        return 0

    async def count(self, *, action=None, start_at=None, end_at=None):
        return 0

    async def count_by_action_prefix(self, prefix, *, start_at=None, end_at=None):
        return 0

    async def list(self, *, action=None, start_at=None, end_at=None, limit=20):
        return []

    async def list_recent_chained(self, *, limit=20):
        return []


class _FakeUserRepo:
    async def count_by_role(self):
        return {}


class _FakeSessionRepo:
    async def count_created_between(self, start_at, end_at):
        return 0


class _FakeInferenceEndpointRepo:
    async def list_hosts(self):
        return []


class _FakeUow:
    def __init__(self):
        self.db_session = _FakeSession()
        self.audit = _FakeAuditRepo()
        self.user = _FakeUserRepo()
        self.session = _FakeSessionRepo()
        self.inference_endpoint = _FakeInferenceEndpointRepo()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeRunProjection:
    async def execution_metrics(self, *, start_at=None, end_at=None):
        return {
            "run_count": 0,
            "tool_activity_count": 0,
            "approval_request_count": 0,
            "activity_failure_count": 0,
        }


class _FakeEvidenceQuery:
    async def collect(self, *, start_at=None, end_at=None):
        del start_at, end_at
        return ComplianceEvidenceSnapshot(
            audit_count=0,
            operator_scope_count=0,
            operator_sessions=0,
            auth_event_count=0,
            role_distribution={},
            inference_endpoint_hosts=(),
            evidence_export_count=0,
            admin_action_count=0,
            redaction_sample_logs=(),
            timestamp_chain_logs=(),
        )


def _service() -> ComplianceService:
    return ComplianceService(
        _FakeEvidenceQuery(),
        _FakeAudit(),
        _FakeRunProjection(),
        ComplianceRuntimeValues(
            sandbox_driver="docker",
            metrics_token_configured=True,
            audit_signing_key_id="primary",
            signing_key_is_default=False,
        ),
        MutablePolicyReader(),
        FakeReportRenderer(),
    )


@pytest.mark.asyncio
async def test_compliance_report_includes_all_controls(monkeypatch):
    service = _service()

    async def _metrics(*args, **kwargs):
        return {
            "audit_count": 1,
            "run_count": 0,
            "approval_request_count": 0,
            "tool_activity_count": 0,
            "activity_failure_count": 0,
            "operator_scope_count": 0,
            "operator_sessions": 0,
            "auth_event_count": 0,
            "role_distribution": {"admin": 1, "user": 3},
            "inference_endpoint_hosts": [],
            "evidence_export_count": 0,
            "admin_action_count": 0,
            "redaction_spot_check": [],
            "timestamp_chain_sample": [],
            "sandbox_driver": "docker",
            "metrics_token_configured": True,
            "audit_signing_key_id": "primary",
            "signing_key_is_default": False,
            "runtime_policy": {
                "head_version": 1,
                "execution_revision_id": "execution-1",
                "execution_digest": "sha256:" + "a" * 64,
                "operations_revision_id": "operations-1",
                "operations_digest": "sha256:" + "b" * 64,
            },
        }

    monkeypatch.setattr(service, "_collect_metrics", _metrics)
    report = await service.build_report()
    assert len(report["controls"]) == len(CONTROLS)
    assert report["summary"]["total"] == len(CONTROLS)
    assert report["summary"]["pass"] + report["summary"]["gap"] + report["summary"][
        "attention"
    ] + report["summary"]["not_verified"] + report["summary"]["na"] == len(CONTROLS)
    for item in report["controls"]:
        assert item["status"] in ("pass", "gap", "attention", "not_verified", "na")
    assert set(report["runtime_policy"]) == {
        "head_version",
        "execution_revision_id",
        "execution_digest",
        "operations_revision_id",
        "operations_digest",
    }

    md = service.render_markdown(report)
    assert "合规审计报告" in md


@pytest.mark.asyncio
async def test_collect_metrics_returns_every_key_the_evaluators_read():
    """Contract test (I4): unlike test_compliance_report_includes_all_controls
    above (which monkeypatches `_collect_metrics` away entirely), this runs
    the *real* `_collect_metrics` against an in-memory fake uow and asserts
    its returned dict's keys are a superset of every key the `_eval_*`
    evaluator staticmethods actually read (via `m["..."]` / `m.get("...")`).

    The expected-key set below is hardcoded from a grep over
    compliance_service.py's evaluators, not derived from `_collect_metrics`
    itself -- so renaming/removing a key in `_collect_metrics` without
    updating the evaluator (or vice versa) makes this test fail, instead of
    only surfacing as a silent `KeyError`/`None` read in production.
    """
    service = _service()

    metrics = await service._collect_metrics(None, None)

    expected_keys = {
        "audit_count",
        "run_count",
        "approval_request_count",
        "tool_activity_count",
        "activity_failure_count",
        "operator_scope_count",
        "operator_sessions",
        "auth_event_count",
        "role_distribution",
        "inference_endpoint_hosts",
        "evidence_export_count",
        "admin_action_count",
        "redaction_spot_check",
        "timestamp_chain_sample",
        "sandbox_driver",
        "metrics_token_configured",
        "audit_signing_key_id",
        "signing_key_is_default",
        "runtime_policy",
    }
    assert expected_keys <= metrics.keys()
