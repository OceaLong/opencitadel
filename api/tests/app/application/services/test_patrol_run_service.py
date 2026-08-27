import hashlib
import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.patrol_templates import load_patrol_template
from app.application.services.patrol_run_service import PatrolRunService
from app.domain.errors import ConflictError, ForbiddenError
from app.domain.models.patrol import (
    PATROL_REMEDIATION_TERMINAL_STATUSES,
    PatrolEvidenceRef,
    PatrolFinding,
    PatrolFindingSeverity,
    PatrolFindingStatus,
    PatrolObservationSubmission,
    PatrolPack,
    PatrolPackStatus,
    PatrolRemediation,
    PatrolRemediationAction,
    PatrolRemediationStatus,
    PatrolRun,
    PatrolRunStatus,
    PatrolTriggerType,
    patrol_remediation_params_hash,
)
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import (
    OperationsPolicy,
    PatrolAdmissionMode,
    PatrolOperationsPolicy,
)
from tests.app.application_test_support import (
    NoopGovernanceMetrics,
    RecordingGovernanceMetrics,
)
from tests.runtime_policy_support import MutablePolicyReader


class PatrolRepo:
    def __init__(self, pack):
        self.pack = pack
        self.runs = {}
        self.results = {}
        self.findings = {}
        self.remediations = {}

    async def get_pack(self, pack_id, scope=None, for_update=False):
        return self.pack if pack_id == self.pack.id else None

    async def save_run(self, run):
        self.runs[run.id] = run
        return run

    async def get_run(self, run_id, scope=None, for_update=False):
        return self.runs.get(run_id)

    async def get_run_by_idempotency_key(self, key):
        return next((r for r in self.runs.values() if r.idempotency_key == key), None)

    async def get_active_run_for_pack(self, pack_id):
        return next(
            (
                r
                for r in self.runs.values()
                if r.pack_id == pack_id and r.status.value in {"queued", "running"}
            ),
            None,
        )

    async def save_check_results(self, items):
        for item in items:
            self.results[item.id] = item
        return items

    async def list_check_results(self, run_id, scope=None):
        return [v for v in self.results.values() if v.run_id == run_id]

    async def get_open_finding_by_fingerprint(self, fingerprint):
        return next(
            (
                f
                for f in self.findings.values()
                if f.fingerprint == fingerprint and f.status.value in {"open", "acknowledged"}
            ),
            None,
        )

    async def save_finding(self, finding):
        self.findings[finding.id] = finding
        return finding

    async def get_finding(self, finding_id, scope=None, for_update=False):
        return self.findings.get(finding_id)

    async def list_findings(self, run_id, scope=None):
        return [v for v in self.findings.values() if v.run_id == run_id]

    async def get_run_by_session_id(self, session_id):
        return next((r for r in self.runs.values() if r.session_id == session_id), None)

    async def get_remediation_by_session_id(self, session_id):
        return next((r for r in self.remediations.values() if r.session_id == session_id), None)

    async def get_remediation_by_recheck_run_id(self, run_id):
        return next((r for r in self.remediations.values() if r.recheck_run_id == run_id), None)

    async def save_remediation(self, remediation):
        self.remediations[remediation.id] = remediation
        return remediation

    async def list_runs(self, scope=None, **filters):
        items = [
            run
            for run in self.runs.values()
            if not filters.get("pack_id") or run.pack_id == filters["pack_id"]
        ]
        return sorted(items, key=lambda run: run.created_at, reverse=True)[
            : filters.get("limit", 20)
        ]


class Uow:
    def __init__(self, patrol):
        self.patrol = patrol
        self.execution_commands = object()
        self.inside_transaction = False
        self.session = SimpleNamespace(
            save=AsyncMock(),
            update_status=AsyncMock(),
            get_by_id=AsyncMock(return_value=SimpleNamespace(task_id="task-1")),
        )

    async def __aenter__(self):
        self.inside_transaction = True
        return self

    async def commit(self):
        return None

    async def __aexit__(self, *args):
        self.inside_transaction = False


class CommandIngress:
    def __init__(self, uow):
        self.uow = uow
        self.calls = []

    async def submit(self, command, context, *, sink=None):
        assert self.uow.inside_transaction
        self.calls.append((command, context, sink))
        return command.command_id


class Admission:
    def __init__(self) -> None:
        self.calls = []

    async def admit(self, **kwargs):
        self.calls.append(kwargs)
        return uuid4()


def make_service(
    uow: Uow,
    *,
    policy_reader: MutablePolicyReader | None = None,
    admission: Admission | None = None,
    fixture_replay_enabled: bool = False,
    governance_metrics=None,
) -> PatrolRunService:
    return PatrolRunService(
        lambda: uow,
        run_admission_service=admission or Admission(),
        command_ingress=CommandIngress(uow),
        policy_reader=policy_reader or MutablePolicyReader(),
        fixture_replay_enabled=fixture_replay_enabled,
        governance_metrics=governance_metrics or NoopGovernanceMetrics(),
    )


def make_pack():
    config = load_patrol_template("kubernetes-baseline-v1")
    config.checks = config.checks[:1]
    return PatrolPack(
        owner_user_id="user-1",
        name="Daily",
        slug="daily",
        status=PatrolPackStatus.ACTIVE,
        config=config,
        mcp_server_id="server-1",
        last_validated_version=1,
        validation_summary={
            "ok": True,
            "capability_hash": "c" * 64,
            "enabled_tools": ["get_capabilities", "k8s_workload_summary"],
        },
    )


@pytest.mark.asyncio
async def test_replay_is_controlled_by_injected_restart_bound_flag() -> None:
    repo = PatrolRepo(make_pack())
    disabled = make_service(Uow(repo), fixture_replay_enabled=False)

    with pytest.raises(ForbiddenError, match="replay is disabled"):
        await disabled.replay_run(
            "run-1",
            OwnerScope.personal("user-1"),
            "user-1",
        )

    original = SimpleNamespace(id="run-1", pack_id=repo.pack.id)
    enabled = make_service(Uow(repo), fixture_replay_enabled=True)
    enabled.get_run = AsyncMock(return_value=original)
    enabled.trigger_pack = AsyncMock(return_value=original)

    assert (
        await enabled.replay_run(
            original.id,
            OwnerScope.personal("user-1"),
            "user-1",
        )
        is original
    )
    enabled.trigger_pack.assert_awaited_once()


@pytest.mark.asyncio
async def test_paused_operations_policy_denies_before_run_admission() -> None:
    repo = PatrolRepo(make_pack())
    admission = Admission()
    reader = MutablePolicyReader(
        operations=OperationsPolicy(
            patrol=PatrolOperationsPolicy(admission=PatrolAdmissionMode.PAUSED)
        )
    )
    service = make_service(Uow(repo), policy_reader=reader, admission=admission)

    with pytest.raises(ConflictError) as denied:
        await service.trigger_pack(
            repo.pack.id,
            OwnerScope.personal("user-1"),
            "user-1",
            idempotency_key="paused-1",
        )

    assert denied.value.error_key == "apiErrors.patrol.admissionPaused"
    assert admission.calls == []
    assert reader.operations_calls[0][0] is True


@pytest.mark.asyncio
async def test_trigger_and_finalize_are_idempotent_and_server_authoritative():
    repo = PatrolRepo(make_pack())
    uow = Uow(repo)
    service = make_service(uow)
    scope = OwnerScope.personal("user-1")
    with nullcontext():
        run = await service.trigger_pack(repo.pack.id, scope, "user-1", idempotency_key="trigger-1")
        assert (
            await service.trigger_pack(repo.pack.id, scope, "user-1", idempotency_key="trigger-1")
            == run
        )
    observation = {"unavailable_replicas": 1, "not_ready_workloads": ["deployment/api"]}
    digest = hashlib.sha256(
        json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    evidence = [
        PatrolEvidenceRef(
            type="summary",
            ref="collector://evidence/1/summary",
            sha256=digest,
            target_ref=repo.pack.config.target_ref,
        ),
        PatrolEvidenceRef(
            type="resource_refs",
            ref="collector://evidence/1/resources",
            sha256=digest,
            target_ref=repo.pack.config.target_ref,
        ),
    ]
    submission = PatrolObservationSubmission(
        check_id="k8s-workload-availability",
        observation=observation,
        evidence_refs=evidence,
        agent_status="pass",
    )
    final = await service.finalize_run(
        run_id=run.id,
        session_id=run.session_id,
        idempotency_key=run.submission_idempotency_key,
        collector_capability_hash=run.collector_capability_hash,
        submissions=[submission],
    )
    assert final.status.value == "completed_with_findings"
    assert final.fail_count == 1
    assert final.pass_count == 0
    assert len(repo.results) == 1
    assert len(repo.findings) == 1
    again = await service.finalize_run(
        run_id=run.id,
        session_id=run.session_id,
        idempotency_key=run.submission_idempotency_key,
        collector_capability_hash=run.collector_capability_hash,
        submissions=[submission],
    )
    assert again.id == final.id
    assert len(repo.findings) == 1


@pytest.mark.asyncio
async def test_missing_enabled_check_is_finalized_as_error_finding():
    repo = PatrolRepo(make_pack())
    service = make_service(Uow(repo))
    with nullcontext():
        run = await service.trigger_pack(
            repo.pack.id,
            OwnerScope.personal("user-1"),
            "user-1",
            idempotency_key="trigger-2",
        )
    final = await service.finalize_run(
        run_id=run.id,
        session_id=run.session_id,
        idempotency_key=run.submission_idempotency_key,
        collector_capability_hash=run.collector_capability_hash,
        submissions=[],
    )
    assert final.error_count == 1
    assert next(iter(repo.results.values())).error_code == "RESULT_MISSING"
    assert len(repo.findings) == 1


@pytest.mark.asyncio
async def test_bad_evidence_hash_can_never_produce_pass():
    repo = PatrolRepo(make_pack())
    service = make_service(Uow(repo))
    with nullcontext():
        run = await service.trigger_pack(
            repo.pack.id,
            OwnerScope.personal("user-1"),
            "user-1",
            idempotency_key="bad-hash",
        )
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
    now = datetime.now(UTC)
    for index, status in enumerate(
        [PatrolRunStatus.COMPLETED] * 5
        + [PatrolRunStatus.COMPLETED_WITH_FINDINGS, PatrolRunStatus.FAILED]
    ):
        run = PatrolRun(
            pack_id=repo.pack.id,
            execution_run_id=uuid4(),
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
    metrics = await make_service(Uow(repo)).get_pack_metrics(
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
    service = make_service(Uow(repo))
    with nullcontext():
        run = await service.trigger_pack(
            repo.pack.id,
            OwnerScope.personal("user-1"),
            "user-1",
            idempotency_key="timeout-1",
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
async def test_cancel_marks_product_run_cancelled_without_a_legacy_task_port():
    repo = PatrolRepo(make_pack())
    service = make_service(Uow(repo))
    with nullcontext():
        run = await service.trigger_pack(
            repo.pack.id,
            OwnerScope.personal("user-1"),
            "user-1",
            idempotency_key="cancel-1",
        )
    cancelled = await service.cancel_run(
        run.id,
        OwnerScope.personal("user-1"),
        "user-1",
    )
    assert cancelled.status == PatrolRunStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_persists_formal_command_in_product_transaction():
    repo = PatrolRepo(make_pack())
    uow = Uow(repo)
    commands = CommandIngress(uow)
    service = PatrolRunService(
        lambda: uow,
        run_admission_service=Admission(),
        command_ingress=commands,
        policy_reader=MutablePolicyReader(),
        fixture_replay_enabled=False,
        governance_metrics=NoopGovernanceMetrics(),
    )
    scope = OwnerScope.personal("user-1")
    with nullcontext():
        run = await service.trigger_pack(
            repo.pack.id,
            scope,
            "user-1",
            idempotency_key="cancel-atomic",
        )
    run.execution_run_id = uuid4()
    await repo.save_run(run)

    cancelled = await service.cancel_run(run.id, scope, "user-1")

    assert cancelled.status == PatrolRunStatus.CANCELLED
    assert len(commands.calls) == 1
    command, _, sink = commands.calls[0]
    assert command.command_type == "CancelRun"
    assert command.run_id == run.execution_run_id
    assert sink is uow.execution_commands


@pytest.mark.asyncio
async def test_cancel_rejects_a_run_without_formal_execution_before_mutation():
    repo = PatrolRepo(make_pack())
    uow = Uow(repo)
    commands = CommandIngress(uow)
    service = PatrolRunService(
        lambda: uow,
        run_admission_service=Admission(),
        command_ingress=commands,
        policy_reader=MutablePolicyReader(),
        fixture_replay_enabled=False,
        governance_metrics=NoopGovernanceMetrics(),
    )
    scope = OwnerScope.personal("user-1")
    with nullcontext():
        run = await service.trigger_pack(
            repo.pack.id,
            scope,
            "user-1",
            idempotency_key="cancel-missing-formal-run",
        )
    run.execution_run_id = None
    await repo.save_run(run)

    with pytest.raises(ConflictError, match="formal execution"):
        await service.cancel_run(run.id, scope, "user-1")

    assert run.status == PatrolRunStatus.RUNNING
    assert commands.calls == []


def _k8s_evidence(observation: dict, target_ref: str) -> list[PatrolEvidenceRef]:
    digest = hashlib.sha256(
        json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return [
        PatrolEvidenceRef(
            type="summary",
            ref="collector://evidence/1/summary",
            sha256=digest,
            target_ref=target_ref,
        ),
        PatrolEvidenceRef(
            type="resource_refs",
            ref="collector://evidence/1/resources",
            sha256=digest,
            target_ref=target_ref,
        ),
    ]


async def _finalize_original_failure(repo, service, scope) -> tuple[PatrolRun, PatrolFinding, str]:
    """Trigger + finalize a MANUAL run whose k8s-workload-availability check
    FAILs, producing one open Finding. Returns (run, finding, check_result_id)
    for the caller to build a PatrolRemediation against."""
    with nullcontext():
        run = await service.trigger_pack(repo.pack.id, scope, "user-1", idempotency_key="orig-1")
    observation = {"unavailable_replicas": 1, "not_ready_workloads": ["deployment/api"]}
    submission = PatrolObservationSubmission(
        check_id="k8s-workload-availability",
        observation=observation,
        evidence_refs=_k8s_evidence(observation, repo.pack.config.target_ref),
    )
    finalized = await service.finalize_run(
        run_id=run.id,
        session_id=run.session_id,
        idempotency_key=run.submission_idempotency_key,
        collector_capability_hash=run.collector_capability_hash,
        submissions=[submission],
    )
    assert finalized.fail_count == 1
    finding = next(iter(repo.findings.values()))
    check_result = next(iter(v for v in repo.results.values() if v.run_id == run.id))
    return run, finding, check_result.id


def _remediation_for(
    run: PatrolRun, finding: PatrolFinding, check_result_id: str
) -> PatrolRemediation:
    action, namespace, workload, kind, params = (
        PatrolRemediationAction.RESTART_WORKLOAD,
        "opencitadel",
        "deployment/api",
        "Deployment",
        {},
    )
    return PatrolRemediation(
        pack_id=run.pack_id,
        run_id=run.id,
        finding_id=finding.id,
        check_result_id=check_result_id,
        fingerprint=finding.fingerprint,
        action=action,
        target_namespace=namespace,
        target_workload=workload,
        target_kind=kind,
        params=params,
        params_hash=patrol_remediation_params_hash(action.value, namespace, workload, kind, params),
        idempotency_key="rem:1",
        status=PatrolRemediationStatus.EXECUTED,
        created_by="user-1",
    )


@pytest.mark.asyncio
async def test_remediation_run_pass_resolves_finding_and_verifies_remediation():
    """finalize_run(trigger_type=REMEDIATION): the recheck run's check now
    PASSes -> the original open Finding is auto-resolved (decided_by =
    "system:remediation", reason references the remediation) and the
    remediation itself moves to VERIFIED."""
    repo = PatrolRepo(make_pack())
    metrics = RecordingGovernanceMetrics()
    service = make_service(Uow(repo), governance_metrics=metrics)
    scope = OwnerScope.personal("user-1")
    run, finding, check_result_id = await _finalize_original_failure(repo, service, scope)

    remediation = _remediation_for(run, finding, check_result_id)
    repo.remediations[remediation.id] = remediation

    with nullcontext():
        recheck_run = await service.trigger_pack(
            repo.pack.id,
            scope,
            "user-1",
            idempotency_key="recheck-1",
            trigger_type=PatrolTriggerType.REMEDIATION,
        )
    remediation.recheck_run_id = recheck_run.id
    repo.remediations[remediation.id] = remediation

    pass_observation = {"unavailable_replicas": 0, "not_ready_workloads": []}
    pass_submission = PatrolObservationSubmission(
        check_id="k8s-workload-availability",
        observation=pass_observation,
        evidence_refs=_k8s_evidence(pass_observation, repo.pack.config.target_ref),
    )
    result = await service.finalize_run(
        run_id=recheck_run.id,
        session_id=recheck_run.session_id,
        idempotency_key=recheck_run.submission_idempotency_key,
        collector_capability_hash=recheck_run.collector_capability_hash,
        submissions=[pass_submission],
    )

    assert result.pass_count == 1
    resolved_finding = repo.findings[finding.id]
    assert resolved_finding.status == PatrolFindingStatus.RESOLVED
    assert resolved_finding.decided_by == "system:remediation"
    assert remediation.id in (resolved_finding.decision_reason or "")
    assert repo.remediations[remediation.id].status == PatrolRemediationStatus.VERIFIED
    # Governance observability (Phase A / Task 2 addendum B): this
    # REMEDIATION-recheck-triggered VERIFIED write is a distinct code path
    # from patrol_remediation_service.py's own transitions (Task 1 already
    # instruments those); it must record the metric too, exactly once.
    assert metrics.remediation_transitions.count("verified") == 1


@pytest.mark.asyncio
async def test_remediation_run_still_failing_marks_remediation_failed():
    """finalize_run(trigger_type=REMEDIATION): the recheck run's check still
    FAILs -> the remediation moves to FAILED(error_code="recheck_failed");
    the Finding is left exactly as the normal WARN/FAIL/ERROR dedup path
    already handles it (still open, occurrence_count incremented)."""
    repo = PatrolRepo(make_pack())
    metrics = RecordingGovernanceMetrics()
    service = make_service(Uow(repo), governance_metrics=metrics)
    scope = OwnerScope.personal("user-1")
    run, finding, check_result_id = await _finalize_original_failure(repo, service, scope)

    remediation = _remediation_for(run, finding, check_result_id)
    repo.remediations[remediation.id] = remediation

    with nullcontext():
        recheck_run = await service.trigger_pack(
            repo.pack.id,
            scope,
            "user-1",
            idempotency_key="recheck-2",
            trigger_type=PatrolTriggerType.REMEDIATION,
        )
    remediation.recheck_run_id = recheck_run.id
    repo.remediations[remediation.id] = remediation

    still_failing_observation = {
        "unavailable_replicas": 1,
        "not_ready_workloads": ["deployment/api"],
    }
    fail_submission = PatrolObservationSubmission(
        check_id="k8s-workload-availability",
        observation=still_failing_observation,
        evidence_refs=_k8s_evidence(still_failing_observation, repo.pack.config.target_ref),
    )
    result = await service.finalize_run(
        run_id=recheck_run.id,
        session_id=recheck_run.session_id,
        idempotency_key=recheck_run.submission_idempotency_key,
        collector_capability_hash=recheck_run.collector_capability_hash,
        submissions=[fail_submission],
    )

    assert result.fail_count == 1
    assert repo.findings[finding.id].status == PatrolFindingStatus.OPEN
    updated = repo.remediations[remediation.id]
    assert updated.status == PatrolRemediationStatus.FAILED
    assert updated.error_code == "recheck_failed"
    # Governance observability (Phase A / Task 2 fix round 1 #2): this is a
    # distinct FAILED-write code path from patrol_remediation_service.py's
    # own 9 instrumented transitions (Task 1) — same reasoning as the
    # VERIFIED branch above, it must record the metric too, exactly once.
    assert metrics.remediation_transitions.count("failed") == 1


@pytest.mark.asyncio
async def test_normal_run_pass_does_not_touch_open_findings():
    """A non-REMEDIATION run's PASS must never auto-resolve an open Finding
    with a matching fingerprint — existing semantics (an operator must
    explicitly decide_finding) are unchanged for MANUAL/SCHEDULE/REPLAY runs."""
    repo = PatrolRepo(make_pack())
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")
    _run, finding, _check_result_id = await _finalize_original_failure(repo, service, scope)

    with nullcontext():
        second_run = await service.trigger_pack(
            repo.pack.id,
            scope,
            "user-1",
            idempotency_key="manual-2",
        )
    pass_observation = {"unavailable_replicas": 0, "not_ready_workloads": []}
    pass_submission = PatrolObservationSubmission(
        check_id="k8s-workload-availability",
        observation=pass_observation,
        evidence_refs=_k8s_evidence(pass_observation, repo.pack.config.target_ref),
    )
    result = await service.finalize_run(
        run_id=second_run.id,
        session_id=second_run.session_id,
        idempotency_key=second_run.submission_idempotency_key,
        collector_capability_hash=second_run.collector_capability_hash,
        submissions=[pass_submission],
    )

    assert result.pass_count == 1
    assert repo.findings[finding.id].status == PatrolFindingStatus.OPEN
    assert repo.findings[finding.id].decided_by is None


@pytest.mark.asyncio
async def test_mark_run_failed_aborts_executed_remediation_when_recheck_run_times_out():
    """Final-review finding I2 (part 2): a REMEDIATION-triggered recheck Run
    that never reaches finalize_run's normal completion (here: it times out
    while the Agent session is still QUEUED/RUNNING, so mark_run_failed is
    what terminates it) must not leave the remediation that dispatched it
    stuck in EXECUTED forever. Only finalize_run's recheck closure normally
    decides EXECUTED -> VERIFIED/FAILED(recheck_failed); if the recheck run
    itself never gets there, mark_run_failed must abort the remediation to
    FAILED(recheck_aborted) so the Finding can accept a new proposal (EXECUTED
    is not in PATROL_REMEDIATION_TERMINAL_STATUSES, so a stuck EXECUTED row
    would otherwise block it forever, same as a stuck PROPOSED row)."""
    repo = PatrolRepo(make_pack())
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")
    run, finding, check_result_id = await _finalize_original_failure(repo, service, scope)

    remediation = _remediation_for(run, finding, check_result_id)
    assert remediation.status == PatrolRemediationStatus.EXECUTED
    repo.remediations[remediation.id] = remediation

    with nullcontext():
        recheck_run = await service.trigger_pack(
            repo.pack.id,
            scope,
            "user-1",
            idempotency_key="recheck-abort-1",
            trigger_type=PatrolTriggerType.REMEDIATION,
        )
    remediation.recheck_run_id = recheck_run.id
    repo.remediations[remediation.id] = remediation

    failed = await service.mark_run_failed(
        recheck_run.session_id,
        error_code="RUN_TIMEOUT",
        error_message="Patrol exceeded 900 seconds",
    )

    assert failed.status == PatrolRunStatus.FAILED
    aborted = repo.remediations[remediation.id]
    assert aborted.status == PatrolRemediationStatus.FAILED
    assert aborted.error_code == "recheck_aborted"
    # This is the exact condition get_active_remediation_for_finding (and the
    # DB partial unique index behind it) use to decide the Finding is free
    # for a new proposal — FAILED must be terminal for that to hold.
    assert aborted.status in PATROL_REMEDIATION_TERMINAL_STATUSES


@pytest.mark.asyncio
async def test_cancel_run_aborts_executed_remediation_for_cancelled_recheck_run():
    """Same as the mark_run_failed case above, but for an operator explicitly
    cancelling a still-queued/running REMEDIATION recheck run via cancel_run
    instead of it timing out."""
    repo = PatrolRepo(make_pack())
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")
    run, finding, check_result_id = await _finalize_original_failure(repo, service, scope)

    remediation = _remediation_for(run, finding, check_result_id)
    repo.remediations[remediation.id] = remediation

    with nullcontext():
        recheck_run = await service.trigger_pack(
            repo.pack.id,
            scope,
            "user-1",
            idempotency_key="recheck-abort-2",
            trigger_type=PatrolTriggerType.REMEDIATION,
        )
    remediation.recheck_run_id = recheck_run.id
    repo.remediations[remediation.id] = remediation

    cancelled = await service.cancel_run(recheck_run.id, scope, "user-1")

    assert cancelled.status == PatrolRunStatus.CANCELLED
    aborted = repo.remediations[remediation.id]
    assert aborted.status == PatrolRemediationStatus.FAILED
    assert aborted.error_code == "recheck_aborted"
    assert aborted.status in PATROL_REMEDIATION_TERMINAL_STATUSES


@pytest.mark.asyncio
async def test_mark_run_failed_does_not_touch_remediation_for_non_remediation_run():
    """A MANUAL run timing out must never touch any remediation row — the
    recheck-abort hook is scoped strictly to trigger_type == REMEDIATION."""
    repo = PatrolRepo(make_pack())
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")
    run, finding, check_result_id = await _finalize_original_failure(repo, service, scope)
    remediation = _remediation_for(run, finding, check_result_id)
    repo.remediations[remediation.id] = remediation

    with nullcontext():
        second_run = await service.trigger_pack(
            repo.pack.id,
            scope,
            "user-1",
            idempotency_key="manual-timeout-1",
        )
    await service.mark_run_failed(
        second_run.session_id, error_code="RUN_TIMEOUT", error_message="timeout"
    )

    assert repo.remediations[remediation.id].status == PatrolRemediationStatus.EXECUTED
    assert repo.remediations[remediation.id].error_code is None
