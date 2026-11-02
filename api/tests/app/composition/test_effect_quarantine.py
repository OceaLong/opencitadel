"""The production after-commit adapter must not revive quarantined Patrol work."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.composition.effects import build_effect_reconciler
from app.domain.execution.effect import EffectOperationSpec, EffectState
from app.domain.models.patrol import PatrolRemediationAction, PatrolRemediationStatus
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import ActivityExecutionPolicy
from tests.app.application.services.test_patrol_remediation_service import (
    Repo,
    Uow,
    _patched,
    make_service,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["unknown", "executed", "verified"])
async def test_conflict_projection_is_sticky_and_never_dispatches_recheck(status):
    repo = Repo()
    patrol_runs = SimpleNamespace(trigger_recheck=AsyncMock())
    patrol = make_service(Uow(repo), patrol_runs=patrol_runs)
    scope = OwnerScope.personal("user-1")
    with _patched():
        remediation = await patrol.propose(
            repo.k8s_finding.id, PatrolRemediationAction.RESTART_WORKLOAD, {}, scope, "user-1"
        )
    remediation.status = PatrolRemediationStatus(status)
    original_receipt = {"state": "applied", "evidence": "original"}
    remediation.actuator_receipt = original_receipt
    spec = EffectOperationSpec(
        operation_id=UUID(remediation.id),
        invocation_id=uuid4(),
        run_id=uuid4(),
        activity_id=uuid4(),
        generation=0,
        provider_identity="ops-actuator",
        contract_digest="a" * 64,
        arguments_digest="b" * 64,
        target_digest="c" * 64,
        idempotency_key="owned",
        capability="dedupe_resolve",
    )
    effect = EffectState(
        spec=spec, state="applied", result_ref="previous-result", integrity_conflict=True
    )
    service = SimpleNamespace(
        store=SimpleNamespace(get=AsyncMock(return_value={"body": effect.model_dump(mode="json")})),
        objects=SimpleNamespace(load_result=AsyncMock()),
    )
    reconciler = build_effect_reconciler(
        resources=SimpleNamespace(postgres=SimpleNamespace(session_factory=None)),
        shared=SimpleNamespace(patrol_remediation_service=patrol),
        service=service,
    )
    await reconciler.after_commit(spec, scope)
    service.objects.load_result.assert_not_awaited()
    assert remediation.status == PatrolRemediationStatus.UNKNOWN
    assert remediation.error_code == "ACTUATOR_EVIDENCE_CONFLICT"
    # A delayed ordinary projection may not clear the committed quarantine.
    await patrol.apply_effect_fact(
        remediation.id,
        scope,
        state="applied",
        result={"after_observation": {}},
        receipt={"state": "applied"},
    )
    assert remediation.status == PatrolRemediationStatus.UNKNOWN
    assert remediation.actuator_receipt == original_receipt
    patrol_runs.trigger_recheck.assert_not_awaited()
    await patrol.resolve_operation(remediation.id, scope, policy=ActivityExecutionPolicy())
    patrol._actuator_client.get_operation.assert_not_awaited()
    await patrol._record_operation_result(remediation, scope, None)
    assert remediation.error_code == "ACTUATOR_EVIDENCE_CONFLICT"
    assert remediation.actuator_receipt == original_receipt
