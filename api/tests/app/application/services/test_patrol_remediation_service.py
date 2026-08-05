from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.application.errors.exceptions import BadRequestError, ConflictError, NotFoundError
from app.application.patrol_templates import load_patrol_template
from app.application.services.patrol_remediation_service import PatrolRemediationService
from app.domain.models.app_config import AppConfig
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


class Repo:
    """In-memory stand-in for DBPatrolRepository, mirroring the fixture style
    used by test_patrol_run_service.py — no real database involved."""

    def __init__(self):
        config = load_patrol_template("kubernetes-baseline-v1")
        self.pack = PatrolPack(
            owner_user_id="user-1", name="Daily", slug="daily", status=PatrolPackStatus.ACTIVE,
            config=config, mcp_server_id="server-1", skill_id="skill-1",
        )
        self.run = PatrolRun(
            pack_id=self.pack.id, pack_version=1,
            pack_snapshot={"config": config.model_dump(mode="json")},
            trigger_type=PatrolTriggerType.MANUAL, idempotency_key="key-1",
        )
        self.k8s_check_result = PatrolCheckResult(
            run_id=self.run.id, check_id="k8s-workload-availability",
            status=PatrolCheckStatus.FAIL, severity=PatrolFindingSeverity.CRITICAL,
            fingerprint="f" * 64,
        )
        self.http_check_result = PatrolCheckResult(
            run_id=self.run.id, check_id="endpoint-health",
            status=PatrolCheckStatus.FAIL, severity=PatrolFindingSeverity.CRITICAL,
            fingerprint="g" * 64,
        )
        self.k8s_finding = PatrolFinding(
            run_id=self.run.id, check_result_id=self.k8s_check_result.id,
            fingerprint=self.k8s_check_result.fingerprint, severity=PatrolFindingSeverity.CRITICAL,
            title="k8s workload unavailable", summary="unavailable replicas",
        )
        self.http_finding = PatrolFinding(
            run_id=self.run.id, check_result_id=self.http_check_result.id,
            fingerprint=self.http_check_result.fingerprint, severity=PatrolFindingSeverity.CRITICAL,
            title="endpoint unhealthy", summary="http probe failing",
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
                item for item in self.remediations.values()
                if item.finding_id == finding_id
                and item.status not in {PatrolRemediationStatus.VERIFIED, PatrolRemediationStatus.FAILED, PatrolRemediationStatus.CANCELLED}
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
        return next((item for item in self.remediations.values() if item.session_id == session_id), None)


class SkillRepo:
    """Fake app.domain.repositories.skill_repository.SkillRepository, mirroring
    the lookup PatrolPackService.create_pack does for "ops-patrol"."""

    def __init__(self, registered: bool = True):
        self._skill = (
            SimpleNamespace(id="skill-remediation-1", slug="ops-patrol-remediation")
            if registered
            else None
        )

    async def get_by_slug(self, slug):
        if slug == "ops-patrol-remediation":
            return self._skill
        return None


class Uow:
    def __init__(self, repo, *, remediation_skill_registered: bool = True):
        self.patrol = repo
        self.session = SimpleNamespace(save=AsyncMock(), update_status=AsyncMock())
        self.skill = SkillRepo(registered=remediation_skill_registered)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def feature_config(enabled: bool = True) -> AppConfig:
    cfg = AppConfig()
    cfg.feature_flags.enable_ops_patrol_remediation = enabled
    return cfg


def _patched(enabled: bool = True):
    return patch(
        "app.application.services.patrol_remediation_service.get_runtime_config",
        return_value=feature_config(enabled),
    )


@pytest.mark.asyncio
async def test_propose_creates_remediation_with_hash_and_session():
    repo = Repo()
    service = PatrolRemediationService(lambda: Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, scope, "user-1", dispatch=False,
        )

    assert remediation.status == PatrolRemediationStatus.PROPOSED
    assert remediation.session_id
    assert remediation.idempotency_key == f"rem:{remediation.id}"
    assert remediation.target_namespace == "opencitadel"
    expected_hash = patrol_remediation_params_hash("restart_workload", "opencitadel", "", "Deployment", {})
    assert remediation.params_hash == expected_hash
    assert repo.remediations[remediation.id].id == remediation.id


@pytest.mark.asyncio
async def test_propose_resolves_real_skill_id_for_session_when_registered():
    """sessions.skill_id is a real FK (infrastructure/models/session.py) — the
    session created by propose() must carry the actual Skill row's id, not a
    bare slug literal, mirroring how PatrolPackService.create_pack resolves
    "ops-patrol" via uow.skill.get_by_slug()."""
    repo = Repo()
    uow = Uow(repo, remediation_skill_registered=True)
    service = PatrolRemediationService(lambda: uow)
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, scope, "user-1", dispatch=False,
        )

    uow.session.save.assert_awaited_once()
    saved_session = uow.session.save.call_args.args[0]
    assert saved_session.id == remediation.session_id
    assert saved_session.skill_id == "skill-remediation-1"


@pytest.mark.asyncio
async def test_propose_rejects_when_remediation_skill_not_registered_with_no_partial_writes():
    repo = Repo()
    uow = Uow(repo, remediation_skill_registered=False)
    service = PatrolRemediationService(lambda: uow)
    scope = OwnerScope.personal("user-1")

    with _patched():
        with pytest.raises(BadRequestError):
            await service.propose(
                repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, scope, "user-1", dispatch=False,
            )

    # No remediation row and no session must be written when the skill is missing.
    assert repo.remediations == {}
    uow.session.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_propose_workload_override_replaces_empty_probe_default():
    """The k8s_workload_summary probe args in the built-in template carry no
    'workload' key, so target_workload defaults to "". The optional request
    override lets the caller supply it explicitly."""
    repo = Repo()
    service = PatrolRemediationService(lambda: Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, scope, "user-1",
            workload="deployment/api", dispatch=False,
        )

    assert remediation.target_workload == "deployment/api"
    expected_hash = patrol_remediation_params_hash("restart_workload", "opencitadel", "deployment/api", "Deployment", {})
    assert remediation.params_hash == expected_hash


@pytest.mark.asyncio
async def test_propose_rejects_blank_workload_override():
    repo = Repo()
    service = PatrolRemediationService(lambda: Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        with pytest.raises(BadRequestError):
            await service.propose(
                repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, scope, "user-1",
                workload="   ", dispatch=False,
            )


@pytest.mark.asyncio
async def test_propose_rejects_action_not_in_catalog():
    """http_probe 类 check 的 finding 提 restart → BadRequest."""
    repo = Repo()
    service = PatrolRemediationService(lambda: Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        with pytest.raises(BadRequestError):
            await service.propose(
                repo.http_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, scope, "user-1", dispatch=False,
            )


@pytest.mark.asyncio
async def test_propose_rejects_when_flag_disabled():
    repo = Repo()
    service = PatrolRemediationService(lambda: Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched(enabled=False):
        with pytest.raises(BadRequestError):
            await service.propose(
                repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, scope, "user-1", dispatch=False,
            )


@pytest.mark.asyncio
async def test_propose_scale_params_validated():
    """scale 缺 replicas / replicas 非正整数 → BadRequest."""
    repo = Repo()
    service = PatrolRemediationService(lambda: Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        with pytest.raises(BadRequestError):
            await service.propose(
                repo.k8s_finding.id, PatrolRemediationAction.SCALE_WORKLOAD, {}, scope, "user-1", dispatch=False,
            )
        with pytest.raises(BadRequestError):
            await service.propose(
                repo.k8s_finding.id, PatrolRemediationAction.SCALE_WORKLOAD, {"replicas": 0}, scope, "user-1", dispatch=False,
            )
        with pytest.raises(BadRequestError):
            await service.propose(
                repo.k8s_finding.id, PatrolRemediationAction.SCALE_WORKLOAD, {"replicas": "3"}, scope, "user-1", dispatch=False,
            )


@pytest.mark.asyncio
async def test_propose_scale_with_positive_replicas_succeeds():
    repo = Repo()
    service = PatrolRemediationService(lambda: Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id, PatrolRemediationAction.SCALE_WORKLOAD, {"replicas": 3}, scope, "user-1", dispatch=False,
        )
    assert remediation.params == {"replicas": 3}


@pytest.mark.asyncio
async def test_propose_rejects_unknown_params_for_action():
    repo = Repo()
    service = PatrolRemediationService(lambda: Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        with pytest.raises(BadRequestError):
            await service.propose(
                repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {"unexpected": 1}, scope, "user-1", dispatch=False,
            )


@pytest.mark.asyncio
async def test_propose_rejects_second_active_remediation_for_same_finding():
    repo = Repo()
    service = PatrolRemediationService(lambda: Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        await service.propose(
            repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, scope, "user-1", dispatch=False,
        )
        with pytest.raises(ConflictError):
            await service.propose(
                repo.k8s_finding.id, PatrolRemediationAction.SCALE_WORKLOAD, {"replicas": 2}, scope, "user-1", dispatch=False,
            )


@pytest.mark.asyncio
async def test_propose_rejects_non_actionable_finding_status():
    repo = Repo()
    repo.k8s_finding.status = PatrolFindingStatus.RESOLVED
    service = PatrolRemediationService(lambda: Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        with pytest.raises(ConflictError):
            await service.propose(
                repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, scope, "user-1", dispatch=False,
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
    service = PatrolRemediationService(lambda: Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        with pytest.raises(BadRequestError):
            await service.propose(
                repo.k8s_finding.id, PatrolRemediationAction.ROLLBACK_WORKLOAD, {"revision": 3}, scope, "user-1", dispatch=False,
            )


@pytest.mark.asyncio
async def test_propose_rollback_workload_impact_summary_does_not_imply_a_chosen_revision():
    """The approval-facing impact_summary must not claim the operator is
    rolling back to a specific revision number — since no such param is
    accepted, the summary must describe only what the Actuator actually
    does: roll back to the previous revision."""
    repo = Repo()
    service = PatrolRemediationService(lambda: Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id, PatrolRemediationAction.ROLLBACK_WORKLOAD, {}, scope, "user-1", dispatch=False,
        )

    assert remediation.params == {}
    assert remediation.impact_summary == "Roll Deployment/<unresolved> in opencitadel back to the previous revision."


@pytest.mark.asyncio
async def test_cross_tenant_get_returns_not_found():
    repo = Repo()
    service = PatrolRemediationService(lambda: Uow(repo))
    owner_scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, owner_scope, "user-1", dispatch=False,
        )

    attacker_scope = OwnerScope.personal("user-2")
    with pytest.raises(NotFoundError):
        await service.get(remediation.id, attacker_scope)

    # And a cross-tenant propose against someone else's Finding must be
    # indistinguishable from a missing Finding.
    with _patched():
        with pytest.raises(NotFoundError):
            await service.propose(
                repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, attacker_scope, "user-2", dispatch=False,
            )


@pytest.mark.asyncio
async def test_cancel_if_pending_cancels_proposed_and_frees_finding_for_reproposal():
    """Regression coverage for cancel_if_pending's original PROPOSED path
    (unchanged by the I2 fix): approval rejected / session cancelled before
    the tool was ever called -> PROPOSED moves to CANCELLED, freeing the
    Finding for a new proposal."""
    repo = Repo()
    service = PatrolRemediationService(lambda: Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, scope, "user-1", dispatch=False,
        )

    await service.cancel_if_pending(remediation.session_id)

    assert repo.remediations[remediation.id].status == PatrolRemediationStatus.CANCELLED
    assert repo.remediations[remediation.id].error_code == "SESSION_TERMINATED"

    with _patched():
        reproposed = await service.propose(
            repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, scope, "user-1", dispatch=False,
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
    service = PatrolRemediationService(lambda: Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, scope, "user-1", dispatch=False,
        )
    stuck = repo.remediations[remediation.id].model_copy(update={"status": PatrolRemediationStatus.EXECUTING})
    repo.remediations[remediation.id] = stuck

    await service.cancel_if_pending(remediation.session_id)

    aborted = repo.remediations[remediation.id]
    assert aborted.status == PatrolRemediationStatus.FAILED
    assert aborted.error_code == "session_terminated_mid_execution"

    with _patched():
        reproposed = await service.propose(
            repo.k8s_finding.id, PatrolRemediationAction.SCALE_WORKLOAD, {"replicas": 2}, scope, "user-1", dispatch=False,
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
    service = PatrolRemediationService(lambda: Uow(repo))
    scope = OwnerScope.personal("user-1")

    with _patched():
        remediation = await service.propose(
            repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, scope, "user-1", dispatch=False,
        )
    done = repo.remediations[remediation.id].model_copy(
        update={"status": PatrolRemediationStatus.VERIFIED, "error_code": None}
    )
    repo.remediations[remediation.id] = done

    await service.cancel_if_pending(remediation.session_id)

    assert repo.remediations[remediation.id].status == PatrolRemediationStatus.VERIFIED
    assert repo.remediations[remediation.id].error_code is None
