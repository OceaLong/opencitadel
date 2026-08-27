"""RBAC and cross-tenant IDOR coverage for Ops Patrol Remediation.

Construction mirrors test_patrol_idor.py (in-memory repo + service call,
NotFoundError for cross-tenant access) and test_resource_mutation_rbac.py
(static route-matrix assertion that every mutating route carries the
require_non_auditor guard).
"""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.application.patrol_templates import load_patrol_template
from app.application.services.patrol_remediation_service import PatrolRemediationService
from app.domain.errors import ForbiddenError, NotFoundError
from app.domain.models.patrol import (
    PatrolCheckResult,
    PatrolCheckStatus,
    PatrolFinding,
    PatrolFindingSeverity,
    PatrolPack,
    PatrolPackStatus,
    PatrolRemediationAction,
    PatrolRun,
    PatrolTriggerType,
)
from app.domain.models.scope import OwnerScope, Principal
from app.domain.models.user import GlobalRole
from app.domain.runtime_policy import (
    OperationsPolicy,
    PatrolOperationsPolicy,
    PatrolRemediationMode,
)
from app.interfaces.auth_dependencies import require_non_auditor
from app.interfaces.endpoints.patrol_routes import router as patrol_router
from tests.app.application_test_support import NoopGovernanceMetrics
from tests.runtime_policy_support import MutablePolicyReader


class Repo:
    def __init__(self):
        config = load_patrol_template("kubernetes-baseline-v1")
        self.pack = PatrolPack(
            owner_user_id="owner-a",
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
        self.check_result = PatrolCheckResult(
            run_id=self.run.id,
            check_id="k8s-workload-availability",
            status=PatrolCheckStatus.FAIL,
            severity=PatrolFindingSeverity.CRITICAL,
            fingerprint="f" * 64,
        )
        self.finding = PatrolFinding(
            run_id=self.run.id,
            check_result_id=self.check_result.id,
            fingerprint=self.check_result.fingerprint,
            severity=PatrolFindingSeverity.CRITICAL,
            title="k8s workload unavailable",
            summary="unavailable replicas",
        )
        self.remediations: dict[str, object] = {}

    def _owned(self, scope):
        return scope is None or scope.user_id == self.pack.owner_user_id

    async def get_finding(self, finding_id, scope=None, for_update=False):
        return self.finding if finding_id == self.finding.id and self._owned(scope) else None

    async def get_run(self, run_id, scope=None, for_update=False):
        return self.run if run_id == self.run.id and self._owned(scope) else None

    async def list_check_results(self, run_id, scope=None):
        return [self.check_result]

    async def get_active_remediation_for_finding(self, finding_id):
        return None

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


class Uow:
    def __init__(self, repo):
        self.patrol = repo
        self.execution_commands = object()
        self.session = SimpleNamespace(save=AsyncMock(), update_status=AsyncMock())

    async def __aenter__(self):
        return self

    async def commit(self):
        return None

    async def __aexit__(self, *args):
        return None


POLICY_READER = MutablePolicyReader(
    operations=OperationsPolicy(
        patrol=PatrolOperationsPolicy(remediation=PatrolRemediationMode.ENABLED)
    )
)


@pytest.mark.asyncio
async def test_attacker_scope_get_remediation_is_indistinguishable_from_missing():
    repo = Repo()
    service = PatrolRemediationService(
        lambda: Uow(repo),
        actuator_client=SimpleNamespace(),
        patrol_run_service=SimpleNamespace(),
        run_admission_service=SimpleNamespace(admit=AsyncMock(return_value=uuid4())),
        policy_reader=POLICY_READER,
        governance_metrics=NoopGovernanceMetrics(),
    )
    owner_scope = OwnerScope.personal("owner-a")

    with nullcontext():
        remediation = await service.propose(
            repo.finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            owner_scope,
            "owner-a",
        )

    attacker_scope = OwnerScope.personal("attacker-b")
    with pytest.raises(NotFoundError):
        await service.get(remediation.id, attacker_scope)

    # A non-existent id and another tenant's real id must raise the same error.
    with pytest.raises(NotFoundError):
        await service.get("does-not-exist", attacker_scope)


@pytest.mark.asyncio
async def test_attacker_scope_propose_against_foreign_finding_returns_not_found():
    repo = Repo()
    service = PatrolRemediationService(
        lambda: Uow(repo),
        actuator_client=SimpleNamespace(),
        patrol_run_service=SimpleNamespace(),
        run_admission_service=SimpleNamespace(admit=AsyncMock(return_value=uuid4())),
        policy_reader=POLICY_READER,
        governance_metrics=NoopGovernanceMetrics(),
    )
    attacker_scope = OwnerScope.personal("attacker-b")

    with (
        nullcontext(),
        pytest.raises(NotFoundError),
    ):
        await service.propose(
            repo.finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            attacker_scope,
            "attacker-b",
        )


def _make_auditor_principal() -> Principal:
    return Principal(user_id="auditor-1", global_role=GlobalRole.AUDITOR, token_version=0)


@pytest.mark.asyncio
async def test_auditor_cannot_pass_the_propose_route_write_guard():
    with (
        patch(
            "app.interfaces.auth_dependencies.get_current_principal",
            new=AsyncMock(return_value=_make_auditor_principal()),
        ),
        pytest.raises(ForbiddenError),
    ):
        await require_non_auditor()


def _write_dependencies():
    return {
        f"{next(iter(route.methods))}:{route.path}": {
            dependency.call for dependency in route.dependant.dependencies
        }
        for route in patrol_router.routes
        if hasattr(route, "dependant")
    }


def test_remediation_routes_require_non_auditor_except_reads():
    routes = _write_dependencies()

    assert require_non_auditor in routes["POST:/patrol-findings/{finding_id}/remediations"]
    assert require_non_auditor not in routes["GET:/patrol-runs/{run_id}/remediations"]
    assert require_non_auditor not in routes["GET:/patrol-remediations/{remediation_id}"]
