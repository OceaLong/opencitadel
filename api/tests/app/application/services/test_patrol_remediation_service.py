from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.patrol_templates import load_patrol_template
from app.application.services.patrol_remediation_service import PatrolRemediationService
from app.domain.errors import BadRequestError, ConflictError, NotFoundError
from app.domain.models.patrol import (
    PatrolCheckResult,
    PatrolCheckStatus,
    PatrolFinding,
    PatrolFindingSeverity,
    PatrolFindingStatus,
    PatrolPack,
    PatrolPackStatus,
    PatrolRemediationAction,
    PatrolRemediationStatus,
    PatrolRun,
    PatrolTriggerType,
    patrol_remediation_params_hash,
)
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import (
    ActivityExecutionPolicy,
    OperationsPolicy,
    PatrolOperationsPolicy,
    PatrolRemediationMode,
)
from tests.app.application_test_support import (
    NoopGovernanceMetrics,
    RecordingGovernanceMetrics,
)
from tests.runtime_policy_support import MutablePolicyReader


class Repo:
    """In-memory stand-in for DBPatrolRepository, mirroring the fixture style
    used by test_patrol_run_service.py — no real database involved."""

    def __init__(self):
        config = load_patrol_template("kubernetes-baseline-v1")
        self.pack = PatrolPack(
            owner_user_id="user-1",
            name="Daily",
            slug="daily",
            status=PatrolPackStatus.ACTIVE,
            config=config,
            mcp_server_id="server-1",
        )
        self.run = PatrolRun(
            pack_id=self.pack.id,
            pack_version=1,
            pack_snapshot={"config": config.model_dump(mode="json")},
            trigger_type=PatrolTriggerType.MANUAL,
            idempotency_key="key-1",
            execution_run_id=uuid4(),
        )
        self.k8s_check_result = PatrolCheckResult(
            run_id=self.run.id,
            check_id="k8s-workload-availability",
            status=PatrolCheckStatus.FAIL,
            severity=PatrolFindingSeverity.CRITICAL,
            fingerprint="f" * 64,
        )
        self.http_check_result = PatrolCheckResult(
            run_id=self.run.id,
            check_id="endpoint-health",
            status=PatrolCheckStatus.FAIL,
            severity=PatrolFindingSeverity.CRITICAL,
            fingerprint="g" * 64,
        )
        self.k8s_finding = PatrolFinding(
            run_id=self.run.id,
            check_result_id=self.k8s_check_result.id,
            fingerprint=self.k8s_check_result.fingerprint,
            severity=PatrolFindingSeverity.CRITICAL,
            title="k8s workload unavailable",
            summary="unavailable replicas",
        )
        self.http_finding = PatrolFinding(
            run_id=self.run.id,
            check_result_id=self.http_check_result.id,
            fingerprint=self.http_check_result.fingerprint,
            severity=PatrolFindingSeverity.CRITICAL,
            title="endpoint unhealthy",
            summary="http probe failing",
        )
        self.remediations: dict[str, object] = {}

    def _owned(self, scope):
        return scope is None or scope.user_id == self.pack.owner_user_id

    async def get_finding(self, finding_id, scope=None, for_update=False):
        for finding in (self.k8s_finding, self.http_finding):
            if finding.id == finding_id and self._owned(scope):
                return finding
        return None

    async def get_run(self, run_id, scope=None, for_update=False):
        if run_id == self.run.id and self._owned(scope):
            return self.run
        return None

    async def list_check_results(self, run_id, scope=None):
        return [self.k8s_check_result, self.http_check_result]

    async def get_active_remediation_for_finding(self, finding_id):
        return next(
            (
                item
                for item in self.remediations.values()
                if item.finding_id == finding_id
                and item.status
                not in {
                    PatrolRemediationStatus.VERIFIED,
                    PatrolRemediationStatus.FAILED,
                    PatrolRemediationStatus.CANCELLED,
                }
            ),
            None,
        )

    async def save_remediation(self, remediation):
        self.remediations[remediation.id] = remediation
        return remediation

    async def get_remediation(self, remediation_id, scope=None, for_update=False):
        remediation = self.remediations.get(remediation_id)
        if remediation is None or not self._owned(scope):
            return None
        return remediation

    async def list_remediations_for_run(self, run_id, scope=None):
        if not self._owned(scope):
            return []
        return [item for item in self.remediations.values() if item.run_id == run_id]

    async def get_remediation_by_session_id(self, session_id):
        return next(
            (item for item in self.remediations.values() if item.session_id == session_id),
            None,
        )


class Uow:
    def __init__(self, repo):
        self.patrol = repo
        self.execution_commands = object()
        self.session = SimpleNamespace(save=AsyncMock(), update_status=AsyncMock())
        self.mcp_server = SimpleNamespace(
            get_by_name=AsyncMock(return_value=SimpleNamespace(id="server-1", enabled=True))
        )

    async def __aenter__(self):
        return self

    async def commit(self):
        return None

    async def __aexit__(self, *args):
        return None


_POLICY_READER = MutablePolicyReader(
    operations=OperationsPolicy(
        patrol=PatrolOperationsPolicy(remediation=PatrolRemediationMode.ENABLED)
    )
)


@contextmanager
def _patched(
    enabled: bool = True,
    *,
    mode: PatrolRemediationMode | None = None,
):
    previous = _POLICY_READER.operations.revision.policy
    _POLICY_READER.set_operations(
        OperationsPolicy(
            patrol=PatrolOperationsPolicy(
                remediation=mode
                or (PatrolRemediationMode.ENABLED if enabled else PatrolRemediationMode.DISABLED),
            )
        )
    )
    try:
        yield
    finally:
        _POLICY_READER.set_operations(previous)


def make_service(
    uow: Uow,
    *,
    actuator=None,
    patrol_runs=None,
    admission=None,
    governance_metrics=None,
) -> PatrolRemediationService:
    return PatrolRemediationService(
        lambda: uow,
        actuator_client=actuator
        or SimpleNamespace(
            get_capabilities=AsyncMock(return_value={"overall_capability_hash": "capability-v1"})
        ),
        patrol_run_service=patrol_runs
        or SimpleNamespace(
            trigger_pack=AsyncMock(return_value=SimpleNamespace(id="default-recheck-run"))
        ),
        run_admission_service=admission or SimpleNamespace(admit=AsyncMock(return_value=uuid4())),
        policy_reader=_POLICY_READER,
        governance_metrics=governance_metrics or NoopGovernanceMetrics(),
    )


@pytest.mark.asyncio
async def test_propose_creates_remediation_with_hash_and_session():
    repo = Repo()
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
        )

    assert remediation.status == PatrolRemediationStatus.PROPOSED
    assert remediation.session_id
    assert remediation.idempotency_key == f"rem:{remediation.id}"
    assert remediation.target_namespace == "opencitadel"
    expected_hash = patrol_remediation_params_hash(
        "restart_workload", "opencitadel", "", "Deployment", {}
    )
    assert remediation.params_hash == expected_hash
    assert repo.remediations[remediation.id].id == remediation.id


@pytest.mark.asyncio
async def test_propose_creates_deterministic_session_without_llm_skill():
    repo = Repo()
    uow = Uow(repo)
    service = make_service(uow)
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
        )

    uow.session.save.assert_awaited_once()
    saved_session = uow.session.save.call_args.args[0]
    assert saved_session.id == remediation.session_id
    assert saved_session.skill_id is None


@pytest.mark.asyncio
async def test_dispatched_remediation_uses_session_source_and_patrol_run_parent():
    repo = Repo()
    repo.run.execution_run_id = uuid4()
    uow = Uow(repo)
    uow.execution_commands = object()
    admission = SimpleNamespace(admit=AsyncMock(return_value=uuid4()))
    service = make_service(uow, admission=admission)
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
        )

    admission.admit.assert_awaited_once()
    request = admission.admit.call_args.kwargs
    assert request["source_entity_type"] == "session"
    assert request["source_entity_id"] == remediation.session_id
    assert request["parent_run_id"] == repo.run.execution_run_id
    assert request["correlation_id"] == repo.run.execution_run_id
    assert request["command_sink"] is uow.execution_commands


@pytest.mark.asyncio
async def test_propose_workload_override_replaces_empty_probe_default():
    """The k8s_workload_summary probe args in the built-in template carry no
    'workload' key, so target_workload defaults to "". The optional request
    override lets the caller supply it explicitly."""
    repo = Repo()
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
            workload="deployment/api",
        )

    assert remediation.target_workload == "deployment/api"
    expected_hash = patrol_remediation_params_hash(
        "restart_workload", "opencitadel", "deployment/api", "Deployment", {}
    )
    assert remediation.params_hash == expected_hash


@pytest.mark.asyncio
async def test_propose_rejects_blank_workload_override():
    repo = Repo()
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched(), pytest.raises(BadRequestError):
        await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
            workload="   ",
        )


@pytest.mark.asyncio
async def test_propose_rejects_action_not_in_catalog():
    """http_probe 类 check 的 finding 提 restart → BadRequest."""
    repo = Repo()
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched(), pytest.raises(BadRequestError):
        await service.propose(
            repo.http_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
        )


@pytest.mark.asyncio
async def test_propose_rejects_when_flag_disabled():
    repo = Repo()
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched(enabled=False), pytest.raises(BadRequestError):
        await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
        )


@pytest.mark.asyncio
async def test_propose_only_policy_allows_proposal_without_execution_enablement():
    repo = Repo()
    service = make_service(Uow(repo))

    with _patched(mode=PatrolRemediationMode.PROPOSE_ONLY):
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            OwnerScope.personal("user-1"),
            "user-1",
        )

    assert remediation.status is PatrolRemediationStatus.PROPOSED


@pytest.mark.asyncio
async def test_propose_scale_params_validated():
    """scale 缺 replicas / replicas 非正整数 → BadRequest."""
    repo = Repo()
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        with pytest.raises(BadRequestError):
            await service.propose(
                repo.k8s_finding.id,
                PatrolRemediationAction.SCALE_WORKLOAD,
                {},
                scope,
                "user-1",
            )
        with pytest.raises(BadRequestError):
            await service.propose(
                repo.k8s_finding.id,
                PatrolRemediationAction.SCALE_WORKLOAD,
                {"replicas": 0},
                scope,
                "user-1",
            )
        with pytest.raises(BadRequestError):
            await service.propose(
                repo.k8s_finding.id,
                PatrolRemediationAction.SCALE_WORKLOAD,
                {"replicas": "3"},
                scope,
                "user-1",
            )


@pytest.mark.asyncio
async def test_propose_scale_with_positive_replicas_succeeds():
    repo = Repo()
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.SCALE_WORKLOAD,
            {"replicas": 3},
            scope,
            "user-1",
        )
    assert remediation.params == {"replicas": 3}


@pytest.mark.asyncio
async def test_propose_rejects_unknown_params_for_action():
    repo = Repo()
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched(), pytest.raises(BadRequestError):
        await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {"unexpected": 1},
            scope,
            "user-1",
        )


@pytest.mark.asyncio
async def test_propose_rejects_second_active_remediation_for_same_finding():
    repo = Repo()
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
        )
        with pytest.raises(ConflictError):
            await service.propose(
                repo.k8s_finding.id,
                PatrolRemediationAction.SCALE_WORKLOAD,
                {"replicas": 2},
                scope,
                "user-1",
            )


@pytest.mark.asyncio
async def test_propose_rejects_non_actionable_finding_status():
    repo = Repo()
    repo.k8s_finding.status = PatrolFindingStatus.RESOLVED
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched(), pytest.raises(ConflictError):
        await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
        )


@pytest.mark.asyncio
async def test_propose_rejects_revision_param_for_rollback_workload():
    """Final-review finding I1: the Actuator's rollback_workload tool has no
    `revision` argument — it always rolls back to the workload's immediately
    previous ReplicaSet revision (ops-actuator/src/opencitadel_ops_actuator/
    server.py). Accepting a caller-supplied `revision` param here would let
    an operator "approve" a specific target version the Actuator can never
    actually honor, fabricating approval semantics. It must be rejected the
    same as any other unknown param, not silently accepted and discarded."""
    repo = Repo()
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched(), pytest.raises(BadRequestError):
        await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.ROLLBACK_WORKLOAD,
            {"revision": 3},
            scope,
            "user-1",
        )


@pytest.mark.asyncio
async def test_propose_rollback_workload_impact_summary_does_not_imply_a_chosen_revision():
    """The approval-facing impact_summary must not claim the operator is
    rolling back to a specific revision number — since no such param is
    accepted, the summary must describe only what the Actuator actually
    does: roll back to the previous revision."""
    repo = Repo()
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.ROLLBACK_WORKLOAD,
            {},
            scope,
            "user-1",
        )

    assert remediation.params == {}
    assert (
        remediation.impact_summary
        == "Roll Deployment/<unresolved> in opencitadel back to the previous revision."
    )


@pytest.mark.asyncio
async def test_cross_tenant_get_returns_not_found():
    repo = Repo()
    service = make_service(Uow(repo))
    owner_scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            owner_scope,
            "user-1",
        )

    attacker_scope = OwnerScope.personal("user-2")
    with pytest.raises(NotFoundError):
        await service.get(remediation.id, attacker_scope)

    # And a cross-tenant propose against someone else's Finding must be
    # indistinguishable from a missing Finding.
    with _patched(), pytest.raises(NotFoundError):
        await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            attacker_scope,
            "user-2",
        )


@pytest.mark.asyncio
async def test_execute_resumes_an_interrupted_idempotent_actuator_call():
    """Losing the worker after PROPOSED -> EXECUTING must be recoverable.

    The persisted remediation idempotency key, rather than the Activity lease,
    is the external write identity. Re-delivery therefore resumes the same
    Actuator operation and records its durable outcome.
    """
    repo = Repo()
    uow = Uow(repo)
    uow.mcp_server = SimpleNamespace(
        get_by_name=AsyncMock(return_value=SimpleNamespace(enabled=True))
    )
    actuator = SimpleNamespace(
        get_capabilities=AsyncMock(return_value={"overall_capability_hash": "capability-v1"}),
        execute_action=AsyncMock(
            return_value={
                "action_outcome": "succeeded",
                "before": {"replicas": 1},
                "after": {"replicas": 2},
            }
        ),
    )
    service = make_service(uow, actuator=actuator)
    scope = OwnerScope.personal("user-1")
    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.SCALE_WORKLOAD,
            {"replicas": 2},
            scope,
            "user-1",
            workload="deployment/api",
        )
    remediation.actuator_capability_hash = "capability-v1"
    remediation.status = PatrolRemediationStatus.EXECUTING
    await repo.save_remediation(remediation)

    with _patched():
        result = await service.execute(
            remediation.id,
            remediation.session_id,
            "activity-delivery-id",
            scope,
            policy=ActivityExecutionPolicy(),
        )

    assert result["status"] == PatrolRemediationStatus.EXECUTED.value
    actuator.execute_action.assert_awaited_once()
    assert actuator.execute_action.await_args.args[2]["idempotency_key"] == (
        remediation.idempotency_key
    )
    persisted = repo.remediations[remediation.id]
    assert persisted.status == PatrolRemediationStatus.EXECUTED
    assert persisted.before_observation == {"replicas": 1}
    assert persisted.after_observation == {"replicas": 2}


@pytest.mark.asyncio
async def test_policy_tightening_after_capability_check_denies_actuator_call():
    repo = Repo()
    uow = Uow(repo)
    uow.mcp_server = SimpleNamespace(
        get_by_name=AsyncMock(return_value=SimpleNamespace(enabled=True))
    )

    capability_calls = {"n": 0}

    async def tighten_policy(*_args, **_kwargs):
        capability_calls["n"] += 1
        # The proposal preflight captures the baseline first (call #1); only the
        # post-approval execute-time drift check (call #2) should observe the
        # policy tightening.
        if capability_calls["n"] >= 2:
            _POLICY_READER.set_operations(
                OperationsPolicy(
                    patrol=PatrolOperationsPolicy(remediation=PatrolRemediationMode.DISABLED)
                )
            )
        return {"overall_capability_hash": "capability-v1"}

    actuator = SimpleNamespace(
        get_capabilities=AsyncMock(side_effect=tighten_policy),
        execute_action=AsyncMock(),
    )
    service = make_service(uow, actuator=actuator)
    scope = OwnerScope.personal("user-1")
    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
            workload="deployment/api",
        )
        remediation.actuator_capability_hash = "capability-v1"
        remediation.status = PatrolRemediationStatus.EXECUTING
        await repo.save_remediation(remediation)

        with pytest.raises(BadRequestError) as denied:
            await service.execute(
                remediation.id,
                remediation.session_id,
                "activity-delivery-id",
                scope,
                policy=ActivityExecutionPolicy(),
            )

    assert denied.value.error_key == "apiErrors.patrolRemediation.executionDisabled"
    actuator.execute_action.assert_not_awaited()
    persisted = repo.remediations[remediation.id]
    assert persisted.status is PatrolRemediationStatus.FAILED
    assert persisted.error_code == "POLICY_DENIED"


@pytest.mark.asyncio
async def test_execute_recovery_skips_completed_actuator_call_and_resumes_recheck():
    """A crash after persisting EXECUTED must not repeat the external write."""
    repo = Repo()
    uow = Uow(repo)
    actuator = SimpleNamespace(
        get_capabilities=AsyncMock(return_value={"overall_capability_hash": "capability-v1"}),
        execute_action=AsyncMock(),
    )
    recheck = SimpleNamespace(id="recheck-run-1")
    patrol_runs = SimpleNamespace(trigger_pack=AsyncMock(return_value=recheck))
    service = make_service(
        uow,
        actuator=actuator,
        patrol_runs=patrol_runs,
    )
    scope = OwnerScope.personal("user-1")
    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
            workload="deployment/api",
        )
    # The proposal preflight already captured the baseline; only the execute
    # path is under test here, so forget the propose-time capability call.
    actuator.get_capabilities.reset_mock()
    remediation.status = PatrolRemediationStatus.EXECUTED
    remediation.before_observation = {"generation": 1}
    remediation.after_observation = {"generation": 2}
    await repo.save_remediation(remediation)

    with _patched():
        result = await service.execute(
            remediation.id,
            remediation.session_id,
            "redelivered-activity",
            scope,
            policy=ActivityExecutionPolicy(),
        )

    actuator.get_capabilities.assert_not_awaited()
    actuator.execute_action.assert_not_awaited()
    patrol_runs.trigger_pack.assert_awaited_once()
    assert repo.remediations[remediation.id].recheck_run_id == recheck.id
    assert result["status"] == PatrolRemediationStatus.EXECUTED.value


@pytest.mark.asyncio
async def test_cancel_if_pending_cancels_proposed_and_frees_finding_for_reproposal():
    """Regression coverage for cancel_if_pending's original PROPOSED path
    (unchanged by the I2 fix): approval rejected / session cancelled before
    the tool was ever called -> PROPOSED moves to CANCELLED, freeing the
    Finding for a new proposal."""
    repo = Repo()
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
        )

    await service.cancel_if_pending(remediation.session_id)

    assert repo.remediations[remediation.id].status == PatrolRemediationStatus.CANCELLED
    assert repo.remediations[remediation.id].error_code == "SESSION_TERMINATED"

    with _patched():
        reproposed = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
        )
    assert reproposed.id != remediation.id
    assert reproposed.status == PatrolRemediationStatus.PROPOSED


@pytest.mark.asyncio
async def test_cancel_if_pending_fails_executing_session_and_frees_finding_for_reproposal():
    """Final-review finding I2 (part 1): if the worker process dies while a
    remediation session is EXECUTING — execute() has already flipped the row
    to EXECUTING (before releasing its row lock and calling the Actuator)
    but the session terminates before an outcome is ever recorded —
    on_session_terminal's cancel_if_pending must not silently no-op the way
    it used to for anything other than PROPOSED. Left stuck in EXECUTING,
    the remediation would permanently block any new proposal for the same
    Finding, because EXECUTING is not in PATROL_REMEDIATION_TERMINAL_STATUSES
    (and the DB partial unique index agrees — see the alembic revision that
    creates patrol_remediations)."""
    repo = Repo()
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
        )
    stuck = repo.remediations[remediation.id].model_copy(
        update={"status": PatrolRemediationStatus.EXECUTING}
    )
    repo.remediations[remediation.id] = stuck

    await service.cancel_if_pending(remediation.session_id)

    aborted = repo.remediations[remediation.id]
    assert aborted.status == PatrolRemediationStatus.FAILED
    assert aborted.error_code == "session_terminated_mid_execution"

    with _patched():
        reproposed = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.SCALE_WORKLOAD,
            {"replicas": 2},
            scope,
            "user-1",
        )
    assert reproposed.id != remediation.id
    assert reproposed.status == PatrolRemediationStatus.PROPOSED


@pytest.mark.asyncio
async def test_cancel_if_pending_is_noop_for_already_terminal_remediation():
    """A remediation that already reached VERIFIED/FAILED/CANCELLED before
    the session terminated must not be touched again — cancel_if_pending is
    a best-effort cleanup for sessions that ended *without* the service
    itself already having decided a terminal outcome."""
    repo = Repo()
    service = make_service(Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
        )
    done = repo.remediations[remediation.id].model_copy(
        update={"status": PatrolRemediationStatus.VERIFIED, "error_code": None}
    )
    repo.remediations[remediation.id] = done

    await service.cancel_if_pending(remediation.session_id)

    assert repo.remediations[remediation.id].status == PatrolRemediationStatus.VERIFIED
    assert repo.remediations[remediation.id].error_code is None


@pytest.mark.asyncio
async def test_propose_records_proposed_remediation_transition():
    repo = Repo()
    metrics = RecordingGovernanceMetrics()
    service = make_service(Uow(repo), governance_metrics=metrics)
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
        )

    assert remediation.status == PatrolRemediationStatus.PROPOSED
    assert metrics.remediation_transitions.count("proposed") == 1


@pytest.mark.asyncio
async def test_cancel_if_pending_records_cancelled_remediation_transition():
    repo = Repo()
    metrics = RecordingGovernanceMetrics()
    service = make_service(Uow(repo), governance_metrics=metrics)
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
        )
    await service.cancel_if_pending(remediation.session_id)

    assert repo.remediations[remediation.id].status == PatrolRemediationStatus.CANCELLED
    assert metrics.remediation_transitions.count("cancelled") == 1


@pytest.mark.asyncio
async def test_propose_persists_pre_approval_capability_baseline():
    # Regression: propose never captured the Actuator capability baseline, so
    # actuator_capability_hash stayed None and every approved remediation failed
    # closed at execute() with CAPABILITY_BASELINE_MISSING.
    repo = Repo()
    actuator = SimpleNamespace(
        get_capabilities=AsyncMock(return_value={"overall_capability_hash": "cap-baseline-9"}),
        execute_action=AsyncMock(),
    )
    service = make_service(Uow(repo), actuator=actuator)
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
            workload="deployment/api",
        )

    assert remediation.actuator_capability_hash == "cap-baseline-9"
    actuator.get_capabilities.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_fails_closed_when_capability_baseline_missing():
    # Regression: the CAPABILITY_BASELINE_MISSING fail-closed branch had zero
    # coverage because every execute test manually injected a baseline hash.
    repo = Repo()
    actuator = SimpleNamespace(
        get_capabilities=AsyncMock(return_value={"overall_capability_hash": "cap-1"}),
        execute_action=AsyncMock(),
    )
    service = make_service(Uow(repo), actuator=actuator)
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            scope,
            "user-1",
            workload="deployment/api",
        )
        # Simulate the historical defect: no baseline persisted before approval.
        remediation.actuator_capability_hash = None
        remediation.status = PatrolRemediationStatus.EXECUTING
        await repo.save_remediation(remediation)

        with pytest.raises(ConflictError) as missing:
            await service.execute(
                remediation.id,
                remediation.session_id,
                "activity-delivery",
                scope,
                policy=ActivityExecutionPolicy(),
            )

    assert missing.value.error_key == "apiErrors.patrolRemediation.capabilityBaselineMissing"
    actuator.execute_action.assert_not_awaited()
    persisted = repo.remediations[remediation.id]
    assert persisted.status is PatrolRemediationStatus.FAILED
    assert persisted.error_code == "CAPABILITY_BASELINE_MISSING"
