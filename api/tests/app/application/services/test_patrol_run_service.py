import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.application.patrol_templates import load_patrol_template
from app.application.services.patrol_run_service import PatrolRunService
from app.domain.models.app_config import AppConfig
from app.domain.models.patrol import (
    PatrolEvidenceRef,
    PatrolFinding,
    PatrolFindingSeverity,
    PatrolFindingStatus,
    PatrolObservationSubmission,
    PatrolPack,
    PatrolPackStatus,
    PatrolRun,
    PatrolRunStatus,
    PatrolTriggerType,
)
from app.domain.models.scope import OwnerScope


class PatrolRepo:
    def __init__(self, pack):
        self.pack = pack
        self.runs = {}
        self.results = {}
        self.findings = {}
    async def get_pack(self, pack_id, scope=None, for_update=False): return self.pack if pack_id == self.pack.id else None
    async def save_run(self, run): self.runs[run.id] = run; return run
    async def get_run(self, run_id, scope=None, for_update=False): return self.runs.get(run_id)
    async def get_run_by_idempotency_key(self, key): return next((r for r in self.runs.values() if r.idempotency_key == key), None)
    async def get_active_run_for_pack(self, pack_id): return next((r for r in self.runs.values() if r.pack_id == pack_id and r.status.value in {"queued", "running"}), None)
    async def save_check_results(self, items): self.results.update({i.check_id: i for i in items}); return items
    async def list_check_results(self, run_id, scope=None): return [v for v in self.results.values() if v.run_id == run_id]
    async def get_open_finding_by_fingerprint(self, fingerprint): return next((f for f in self.findings.values() if f.fingerprint == fingerprint and f.status.value in {"open", "acknowledged"}), None)
    async def save_finding(self, finding): self.findings[finding.id] = finding; return finding
    async def list_findings(self, run_id, scope=None): return [v for v in self.findings.values() if v.run_id == run_id]
    async def get_run_by_session_id(self, session_id): return next((r for r in self.runs.values() if r.session_id == session_id), None)
    async def list_runs(self, scope=None, **filters):
        items = [run for run in self.runs.values() if not filters.get("pack_id") or run.pack_id == filters["pack_id"]]
        return sorted(items, key=lambda run: run.created_at, reverse=True)[: filters.get("limit", 20)]


class Uow:
    def __init__(self, patrol):
        self.patrol = patrol
        self.session = SimpleNamespace(
            save=AsyncMock(),
            update_status=AsyncMock(),
            get_by_id=AsyncMock(return_value=SimpleNamespace(task_id="task-1")),
        )
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None


def feature_config():
    cfg = AppConfig()
    cfg.feature_flags.enable_ops_patrol = True
    return cfg


def make_pack():
    config = load_patrol_template("kubernetes-baseline-v1")
    config.checks = config.checks[:1]
    return PatrolPack(
        owner_user_id="user-1", name="Daily", slug="daily", status=PatrolPackStatus.ACTIVE,
        config=config, mcp_server_id="server-1", skill_id="skill-1",
        last_validated_version=1,
        validation_summary={"ok": True, "capability_hash": "c" * 64, "enabled_tools": ["get_capabilities", "k8s_workload_summary"]},
    )


@pytest.mark.asyncio
async def test_trigger_and_finalize_are_idempotent_and_server_authoritative():
    repo = PatrolRepo(make_pack())
    uow = Uow(repo)
    service = PatrolRunService(lambda: uow)
    scope = OwnerScope.personal("user-1")
    with patch("app.application.services.patrol_run_service.get_runtime_config", return_value=feature_config()):
        run = await service.trigger_pack(repo.pack.id, scope, "user-1", idempotency_key="trigger-1", dispatch=False)
        assert await service.trigger_pack(repo.pack.id, scope, "user-1", idempotency_key="trigger-1", dispatch=False) == run
    observation = {"unavailable_replicas": 1, "not_ready_workloads": ["deployment/api"]}
    digest = hashlib.sha256(json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    evidence = [
        PatrolEvidenceRef(type="summary", ref="collector://evidence/1/summary", sha256=digest, target_ref=repo.pack.config.target_ref),
        PatrolEvidenceRef(type="resource_refs", ref="collector://evidence/1/resources", sha256=digest, target_ref=repo.pack.config.target_ref),
    ]
    submission = PatrolObservationSubmission(
        check_id="k8s-workload-availability",
        observation=observation,
        evidence_refs=evidence,
        agent_status="pass",
    )
    final = await service.finalize_run(
        run_id=run.id, session_id=run.session_id, idempotency_key=run.submission_idempotency_key,
        collector_capability_hash=run.collector_capability_hash, submissions=[submission],
    )
    assert final.status.value == "completed_with_findings"
    assert final.fail_count == 1 and final.pass_count == 0
    assert len(repo.results) == 1 and len(repo.findings) == 1
    again = await service.finalize_run(
        run_id=run.id, session_id=run.session_id, idempotency_key=run.submission_idempotency_key,
        collector_capability_hash=run.collector_capability_hash, submissions=[submission],
    )
    assert again.id == final.id and len(repo.findings) == 1


@pytest.mark.asyncio
async def test_missing_enabled_check_is_finalized_as_error_finding():
    repo = PatrolRepo(make_pack())
    service = PatrolRunService(lambda: Uow(repo))
    with patch("app.application.services.patrol_run_service.get_runtime_config", return_value=feature_config()):
        run = await service.trigger_pack(repo.pack.id, OwnerScope.personal("user-1"), "user-1", idempotency_key="trigger-2", dispatch=False)
    final = await service.finalize_run(
        run_id=run.id, session_id=run.session_id, idempotency_key=run.submission_idempotency_key,
        collector_capability_hash=run.collector_capability_hash, submissions=[],
    )
    assert final.error_count == 1
    assert next(iter(repo.results.values())).error_code == "RESULT_MISSING"
    assert len(repo.findings) == 1


@pytest.mark.asyncio
async def test_bad_evidence_hash_can_never_produce_pass():
    repo = PatrolRepo(make_pack())
    service = PatrolRunService(lambda: Uow(repo))
    with patch("app.application.services.patrol_run_service.get_runtime_config", return_value=feature_config()):
        run = await service.trigger_pack(repo.pack.id, OwnerScope.personal("user-1"), "user-1", idempotency_key="bad-hash", dispatch=False)
    submission = PatrolObservationSubmission(
        check_id="k8s-workload-availability",
        observation={"unavailable_replicas": 0, "not_ready_workloads": []},
        evidence_refs=[
            PatrolEvidenceRef(
                type="summary",
                ref="collector://evidence/1/summary",
                sha256="a" * 64,
                target_ref=repo.pack.config.target_ref,
                verified=True,
            ),
            PatrolEvidenceRef(
                type="resource_refs",
                ref="collector://evidence/1/resources",
                sha256="a" * 64,
                target_ref=repo.pack.config.target_ref,
                verified=True,
            ),
        ],
    )
    final = await service.finalize_run(
        run_id=run.id,
        session_id=run.session_id,
        idempotency_key=run.submission_idempotency_key,
        collector_capability_hash=run.collector_capability_hash,
        submissions=[submission],
    )
    result = next(iter(repo.results.values()))
    assert final.pass_count == 0
    assert result.status.value == "error"
    assert result.error_code == "EVIDENCE_INCOMPLETE"


@pytest.mark.asyncio
async def test_metrics_use_scheduled_success_and_review_median_without_zero_fill():
    repo = PatrolRepo(make_pack())
    now = datetime.now(timezone.utc)
    for index, status in enumerate(
        [PatrolRunStatus.COMPLETED] * 5
        + [PatrolRunStatus.COMPLETED_WITH_FINDINGS, PatrolRunStatus.FAILED]
    ):
        run = PatrolRun(
            pack_id=repo.pack.id,
            pack_version=1,
            pack_snapshot={},
            trigger_type=PatrolTriggerType.SCHEDULE,
            status=status,
            idempotency_key=f"metric-{index}",
            created_at=now - timedelta(days=index),
            first_reviewed_at=now - timedelta(minutes=10) if index == 5 else None,
        )
        repo.runs[run.id] = run
        if index == 5:
            finding = PatrolFinding(
                run_id=run.id,
                check_result_id="result-1",
                fingerprint="f" * 64,
                severity=PatrolFindingSeverity.WARNING,
                status=PatrolFindingStatus.FALSE_POSITIVE,
                title="warning",
                summary="warning",
                decided_by="user-1",
                decided_at=now,
                decision_reason="known test",
            )
            repo.findings[finding.id] = finding
    metrics = await PatrolRunService(lambda: Uow(repo)).get_pack_metrics(
        repo.pack.id,
        OwnerScope.personal("user-1"),
    )
    assert metrics == {
        "sample_size": 7,
        "scheduled_run_count": 7,
        "scheduled_success_rate": 6 / 7,
        "finding_count": 1,
        "false_positive_count": 1,
        "median_review_minutes": 10,
    }


@pytest.mark.asyncio
async def test_run_timeout_marks_every_unfinished_enabled_check_error():
    repo = PatrolRepo(make_pack())
    service = PatrolRunService(lambda: Uow(repo))
    with patch("app.application.services.patrol_run_service.get_runtime_config", return_value=feature_config()):
        run = await service.trigger_pack(
            repo.pack.id,
            OwnerScope.personal("user-1"),
            "user-1",
            idempotency_key="timeout-1",
            dispatch=False,
        )
    failed = await service.mark_run_failed(
        run.session_id,
        error_code="RUN_TIMEOUT",
        error_message="Patrol exceeded 900 seconds",
    )
    result = next(iter(repo.results.values()))
    assert failed.status == PatrolRunStatus.FAILED
    assert failed.error_count == 1
    assert failed.evidence_completeness == 0
    assert failed.summary["error_code"] == "RUN_TIMEOUT"
    assert result.status.value == "error"
    assert result.error_code == "RUN_TIMEOUT"
    assert len(repo.findings) == 1


@pytest.mark.asyncio
async def test_cancel_requests_cooperative_task_stop():
    repo = PatrolRepo(make_pack())
    task_state = SimpleNamespace(request_cancel=AsyncMock())
    service = PatrolRunService(lambda: Uow(repo), task_state_port=task_state)
    with patch("app.application.services.patrol_run_service.get_runtime_config", return_value=feature_config()):
        run = await service.trigger_pack(
            repo.pack.id,
            OwnerScope.personal("user-1"),
            "user-1",
            idempotency_key="cancel-1",
            dispatch=False,
        )
    cancelled = await service.cancel_run(
        run.id,
        OwnerScope.personal("user-1"),
        "user-1",
    )
    assert cancelled.status == PatrolRunStatus.CANCELLED
    task_state.request_cancel.assert_awaited_once_with("task-1")
