"""In-process replay of fixture 21 (21-remediation-crashloop) against the real
PatrolRemediationService + PatrolRunService state machine -- no LLM, no
cluster, no worker/Redis.

deploy/patrol-demo/fixtures/21-remediation-crashloop/expected.json carries a
`remediation` block (`expected_status_sequence`,
`recheck_expected_results`) that no production code and no existing test
consumes: test_patrol_golden_fixtures.py explicitly excludes any fixture with
a `remediation` key (it only replays the read-only PatrolAssertionEngine, and
this fixture's expected_write_operations is nonzero -- see that file's
GOLDEN_EXPECTED_PATHS comment). This suite drives the actual propose ->
execute -> auto-recheck -> finalize_run closure loop
(patrol_remediation_service.py / patrol_run_service.py) and asserts the
observed status sequence and recheck resolution against those exact fixture
values, so a shape change to either key breaks a real assertion instead of
going unnoticed.

Fakes are composed from the two existing service-level suites rather than a
new parallel fake ecosystem:
  - PatrolRepo/Uow/_k8s_evidence from test_patrol_run_service.py (trigger_pack
    / finalize_run's run+finding+remediation-by-recheck-run_id storage).
  - SkillRepo from test_patrol_remediation_service.py (propose()'s
    ops-patrol-remediation Skill lookup for the session FK).
  - _FakeActuatorClient/_MCPServerRepo/_make_server from
    test_remediation_governance_invariants.py (execute()'s Actuator capability
    baseline + write call).
Only the union of remediation-lookup methods PatrolRemediationService needs
that PatrolRepo doesn't already have (get_active_remediation_for_finding,
get_remediation) is added here, plus a status-history recorder on
save_remediation to observe the EXECUTING transition that trigger_pack's
caller (execute()) never returns to its caller directly.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.application.patrol_templates import load_patrol_template
from app.application.services.patrol_remediation_service import PatrolRemediationService
from app.application.services.patrol_run_service import PatrolRunService
from app.domain.models.app_config import AppConfig
from app.domain.models.patrol import (
    PatrolFindingStatus,
    PatrolObservationSubmission,
    PatrolPack,
    PatrolPackStatus,
    PatrolRemediationAction,
    PatrolRemediationStatus,
)
from app.domain.models.scope import OwnerScope

from tests.app.application.services.test_patrol_run_service import (
    Uow as _RunUow,
    PatrolRepo as _RunPatrolRepo,
    _k8s_evidence,
)
from tests.app.application.services.test_patrol_remediation_service import SkillRepo
from tests.app.contracts.test_remediation_governance_invariants import (
    _FakeActuatorClient,
    _MCPServerRepo,
    _make_server,
)


FIXTURE_PATH = (
    Path(__file__).parents[4]
    / "deploy"
    / "patrol-demo"
    / "fixtures"
    / "21-remediation-crashloop"
    / "expected.json"
)
EXPECTED = json.loads(FIXTURE_PATH.read_text())

# Observations mirroring test_patrol_golden_fixtures.py's "03-crashloop"
# OVERRIDES (the read-only sibling of this remediation fixture): both checks
# the fixture's fault touches (CrashLoopBackOff) fail together, and both
# recover together once restart_workload actually fixes the workload.
FAIL_WORKLOAD_OBS = {"unavailable_replicas": 1, "not_ready_workloads": ["deployment/fixture-remediation-crashloop"]}
FAIL_RESTART_OBS = {"restart_count_1h": 11}
PASS_WORKLOAD_OBS = {"unavailable_replicas": 0, "not_ready_workloads": []}
PASS_RESTART_OBS = {"restart_count_1h": 0}

_OBSERVATIONS = {
    "k8s-workload-availability": {"pass": PASS_WORKLOAD_OBS, "fail": FAIL_WORKLOAD_OBS},
    "k8s-restart-spike": {"pass": PASS_RESTART_OBS, "fail": FAIL_RESTART_OBS},
}


class Repo(_RunPatrolRepo):
    """_RunPatrolRepo (test_patrol_run_service.Uow's backing store) already
    has everything finalize_run's REMEDIATION-recheck closure needs
    (save_remediation, get_remediation_by_recheck_run_id). Add the two lookups
    PatrolRemediationService.propose()/execute() additionally require, plus a
    per-remediation status history so the mid-flight EXECUTING write (never
    otherwise observable -- execute() only returns the final EXECUTED dict)
    can be asserted against expected_status_sequence."""

    def __init__(self, pack):
        super().__init__(pack)
        self.remediation_status_history: dict[str, list[str]] = {}

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

    async def get_remediation(self, remediation_id, scope=None, for_update=False):
        return self.remediations.get(remediation_id)

    async def save_remediation(self, remediation):
        history = self.remediation_status_history.setdefault(remediation.id, [])
        if not history or history[-1] != remediation.status.value:
            history.append(remediation.status.value)
        return await super().save_remediation(remediation)


class Uow(_RunUow):
    def __init__(self, patrol, server):
        super().__init__(patrol)
        self.skill = SkillRepo(registered=True)
        self.mcp_server = _MCPServerRepo(server)


class _FakeTask:
    """Stand-in for RedisStreamTask -- execute()'s auto-recheck dispatch calls
    trigger_pack without dispatch=False, so unlike every other trigger_pack
    call in test_patrol_run_service.py (which control dispatch directly) this
    path always attempts a real worker dispatch. No Redis/worker exists in
    this in-process suite, so RedisStreamTask.create_for_session itself is
    replaced for the duration of the flow."""

    def __init__(self, session_id: str):
        self.id = f"task-{session_id}"
        self.input_stream = SimpleNamespace(put=AsyncMock())

    async def dispatch_to_worker(self) -> None:
        return None


async def _fake_create_for_session(session_id, *args, **kwargs):
    return _FakeTask(session_id)


def _make_pack() -> PatrolPack:
    # Scoped to the two checks the fixture's CrashLoopBackOff fault actually
    # touches (see test_patrol_golden_fixtures.py OVERRIDES["03-crashloop"]);
    # the other 8 baseline checks are irrelevant to this replay and would
    # otherwise show up as RESULT_MISSING noise on every finalize_run call.
    config = load_patrol_template("kubernetes-baseline-v1")
    config.checks = [check for check in config.checks if check.id in _OBSERVATIONS]
    assert {check.id for check in config.checks} == set(_OBSERVATIONS)
    return PatrolPack(
        owner_user_id="user-1",
        name="Daily",
        slug="daily",
        status=PatrolPackStatus.ACTIVE,
        config=config,
        mcp_server_id="server-1",
        skill_id="skill-1",
        last_validated_version=1,
        validation_summary={"ok": True, "capability_hash": "c" * 64, "enabled_tools": ["get_capabilities", "k8s_workload_summary"]},
    )


def _feature_config() -> AppConfig:
    cfg = AppConfig()
    cfg.feature_flags.enable_ops_patrol = True
    cfg.feature_flags.enable_ops_patrol_remediation = True
    return cfg


def _patched_flags():
    return (
        patch("app.application.services.patrol_run_service.get_runtime_config", return_value=_feature_config()),
        patch("app.application.services.patrol_remediation_service.get_runtime_config", return_value=_feature_config()),
    )


def _patched_runtime():
    patch_run_flags, patch_remediation_flags = _patched_flags()
    return (
        patch_run_flags,
        patch_remediation_flags,
        patch("app.application.services.patrol_run_service.RedisStreamTask.create_for_session", new=_fake_create_for_session),
    )


def _submissions(check_statuses: dict[str, str], target_ref: str) -> list[PatrolObservationSubmission]:
    submissions = []
    for check_id, status in check_statuses.items():
        observation = _OBSERVATIONS[check_id][status]
        submissions.append(
            PatrolObservationSubmission(
                check_id=check_id,
                observation=observation,
                evidence_refs=_k8s_evidence(observation, target_ref),
            )
        )
    return submissions


async def _propose_and_execute():
    """Fixture's fault (CrashLoopBackOff on both checks) -> finalize the
    original failing run -> propose(dispatch=False) -> execute() through to
    EXECUTED with the auto-dispatched recheck run started. Returns everything
    both the VERIFIED-path and the FAILED(recheck_failed)-path tests need to
    finish the loop with their own recheck submissions."""
    pack = _make_pack()
    repo = Repo(pack)
    server = _make_server()
    actuator = _FakeActuatorClient()
    uow_factory = lambda: Uow(repo, server)
    run_service = PatrolRunService(uow_factory)
    remediation_service = PatrolRemediationService(uow_factory, actuator_client=actuator, patrol_run_service=run_service)
    scope = OwnerScope.personal("user-1")

    patch_flags, patch_remediation_flags, patch_task = _patched_runtime()
    with patch_flags, patch_remediation_flags, patch_task:
        run = await run_service.trigger_pack(pack.id, scope, "user-1", idempotency_key="orig-1", dispatch=False)
        fail_submissions = _submissions({"k8s-workload-availability": "fail", "k8s-restart-spike": "fail"}, pack.config.target_ref)
        finalized = await run_service.finalize_run(
            run_id=run.id,
            session_id=run.session_id,
            idempotency_key=run.submission_idempotency_key,
            collector_capability_hash=run.collector_capability_hash,
            submissions=fail_submissions,
        )
        assert finalized.fail_count == 2

        check_result = next(item for item in repo.results.values() if item.run_id == run.id and item.check_id == "k8s-workload-availability")
        finding = next(item for item in repo.findings.values() if item.check_result_id == check_result.id)

        action = PatrolRemediationAction(EXPECTED["remediation"]["action"])
        remediation = await remediation_service.propose(
            finding.id, action, {}, scope, "user-1",
            workload="deployment/fixture-remediation-crashloop", dispatch=False,
        )
        # Bullet 1 of the brief: propose() lands on expected_status_sequence[0].
        assert remediation.status.value == EXPECTED["remediation"]["expected_status_sequence"][0]

        # Simulate TaskRunnerFactory's pre-approval capability baseline
        # preflight (persisted before the tool is ever exposed to the LLM --
        # see _make_remediation's docstring in
        # test_remediation_governance_invariants.py for why this is set
        # directly rather than re-driving TaskRunnerFactory here).
        baselined = repo.remediations[remediation.id].model_copy(update={"actuator_capability_hash": actuator._live_hash})
        repo.remediations[remediation.id] = baselined

        result = await remediation_service.execute(
            remediation_id=remediation.id,
            session_id=remediation.session_id,
            idempotency_key="tool-call-key",
            scope=scope,
        )

    return repo, run_service, remediation, finding, scope, pack, actuator, result


@pytest.mark.asyncio
async def test_execute_reaches_executed_and_dispatches_a_bound_recheck():
    repo, run_service, remediation, finding, scope, pack, actuator, result = await _propose_and_execute()

    # Terminal state after execute() itself is EXECUTED, with EXECUTING
    # observed mid-flight in between (fake uow's status history) -- the
    # first three entries of expected_status_sequence.
    assert repo.remediation_status_history[remediation.id] == EXPECTED["remediation"]["expected_status_sequence"][:3]
    assert result["status"] == "executed"
    assert len(actuator.execute_action_calls) == 1

    stored = repo.remediations[remediation.id]
    assert stored.status == PatrolRemediationStatus.EXECUTED
    assert stored.recheck_run_id is not None
    recheck_run = repo.runs[stored.recheck_run_id]
    assert recheck_run.trigger_type.value == "remediation"
    assert recheck_run.idempotency_key == f"recheck:{remediation.id}"
    assert recheck_run.pack_id == pack.id


@pytest.mark.asyncio
async def test_remediation_state_sequence_matches_fixture_and_resolves_finding():
    """Full loop: recheck run's submissions match fixture
    recheck_expected_results (PASS/PASS) -> remediation VERIFIED, the
    original Finding auto-RESOLVED(decided_by="system:remediation"), and the
    complete observed status history equals expected_status_sequence
    verbatim."""
    repo, run_service, remediation, finding, scope, pack, actuator, result = await _propose_and_execute()
    recheck_run = repo.runs[repo.remediations[remediation.id].recheck_run_id]

    recheck_statuses = {item["check_id"]: item["status"] for item in EXPECTED["remediation"]["recheck_expected_results"]}
    pass_submissions = _submissions(recheck_statuses, pack.config.target_ref)

    patch_flags, patch_remediation_flags = _patched_flags()
    with patch_flags, patch_remediation_flags:
        final = await run_service.finalize_run(
            run_id=recheck_run.id,
            session_id=recheck_run.session_id,
            idempotency_key=recheck_run.submission_idempotency_key,
            collector_capability_hash=recheck_run.collector_capability_hash,
            submissions=pass_submissions,
        )

    assert final.pass_count == 2
    resolved_finding = repo.findings[finding.id]
    assert resolved_finding.status == PatrolFindingStatus.RESOLVED
    assert resolved_finding.decided_by == "system:remediation"
    assert remediation.id in (resolved_finding.decision_reason or "")

    verified = repo.remediations[remediation.id]
    assert verified.status == PatrolRemediationStatus.VERIFIED

    # The money assertion: fixture 21's expected_status_sequence, until now
    # never checked against any executed code path, is exactly the observed
    # status history.
    assert repo.remediation_status_history[remediation.id] == EXPECTED["remediation"]["expected_status_sequence"]


@pytest.mark.asyncio
async def test_recheck_failure_marks_remediation_failed_with_recheck_failed_code():
    """Guard case outside the fixture's own declared expectations: if the
    recheck run still observes the fault (restart_workload didn't actually
    fix it), the remediation must land on FAILED(error_code="recheck_failed"),
    never VERIFIED, and the Finding must stay open rather than being
    auto-resolved."""
    repo, run_service, remediation, finding, scope, pack, actuator, result = await _propose_and_execute()
    recheck_run = repo.runs[repo.remediations[remediation.id].recheck_run_id]

    still_failing_submissions = _submissions(
        {"k8s-workload-availability": "fail", "k8s-restart-spike": "fail"}, pack.config.target_ref
    )

    patch_flags, patch_remediation_flags = _patched_flags()
    with patch_flags, patch_remediation_flags:
        final = await run_service.finalize_run(
            run_id=recheck_run.id,
            session_id=recheck_run.session_id,
            idempotency_key=recheck_run.submission_idempotency_key,
            collector_capability_hash=recheck_run.collector_capability_hash,
            submissions=still_failing_submissions,
        )

    assert final.fail_count == 2
    failed = repo.remediations[remediation.id]
    assert failed.status == PatrolRemediationStatus.FAILED
    assert failed.error_code == "recheck_failed"
    assert repo.findings[finding.id].status == PatrolFindingStatus.OPEN
    assert repo.remediation_status_history[remediation.id][-1] != "verified"


def test_fixture_21_remediation_expectations_are_consumed():
    """Directory guard: fixture 21 must keep carrying the remediation block
    this suite drives against real code, with exactly the shape the suite
    relies on -- catches the block being renamed/reshaped without anyone
    updating the tests that give it teeth."""
    assert "remediation" in EXPECTED
    remediation_spec = EXPECTED["remediation"]
    assert remediation_spec.keys() >= {"action", "target", "expected_status_sequence", "recheck_expected_results"}
    assert remediation_spec["expected_status_sequence"] == ["proposed", "executing", "executed", "verified"]
    assert len(remediation_spec["recheck_expected_results"]) >= 1
    for entry in remediation_spec["recheck_expected_results"]:
        assert set(entry) == {"check_id", "status"}
