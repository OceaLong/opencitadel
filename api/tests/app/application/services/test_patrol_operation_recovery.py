import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.errors import BadRequestError, ConflictError
from app.domain.models.patrol import PatrolRemediationAction, PatrolRemediationStatus
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import ActivityExecutionPolicy
from tests.app.application.services.test_patrol_remediation_service import Repo, Uow, make_service
from tests.app.patrol_actuator_support import capabilities, operation_envelope, target_identity


async def proposed(*, actuator=None):
    repo = Repo()
    actuator = actuator or SimpleNamespace(
        get_capabilities=AsyncMock(return_value=capabilities()),
        get_target=AsyncMock(side_effect=target_identity),
        execute_action=AsyncMock(),
        get_operation=AsyncMock(),
    )
    service = make_service(Uow(repo), actuator=actuator)
    scope = OwnerScope.personal("user-1")
    remediation = await service.propose(
        repo.k8s_finding.id,
        PatrolRemediationAction.SCALE_WORKLOAD,
        {"replicas": 2},
        scope,
        "user-1",
    )
    return service, repo, remediation, actuator, scope


async def execute(service, remediation, scope):
    return await service.execute(
        remediation.id,
        remediation.session_id,
        "untrusted-delivery-key",
        scope,
        policy=ActivityExecutionPolicy(),
    )


@pytest.mark.asyncio
async def test_proposal_freezes_discovered_target_identity_and_provider_key():
    service, repo, remediation, actuator, scope = await proposed()
    assert remediation.actuator_target_uid == "uid-api-1"
    assert remediation.actuator_target_ref == "opencitadel-local"
    assert remediation.actuator_operation_id == remediation.id
    assert len(remediation.actuator_payload_digest) == 64
    actuator.execute_action.return_value = operation_envelope(remediation)
    result = await execute(service, remediation, scope)
    assert result["status"] == "executed"
    arguments = actuator.execute_action.await_args.args[2]
    assert arguments == {
        "namespace": "opencitadel",
        "workload": "api",
        "kind": "deployment",
        "replicas": 2,
        "idempotency_key": f"rem:{remediation.id}",
        "operation_id": remediation.id,
        "payload_digest": remediation.actuator_payload_digest,
        "target_uid": "uid-api-1",
    }
    actuator.get_target.assert_awaited_once()
    assert repo.remediations[remediation.id].actuator_receipt["state"] == "applied"


@pytest.mark.asyncio
async def test_ambiguous_send_queries_original_receipt_and_rechecks_once():
    service, repo, remediation, actuator, scope = await proposed()
    actuator.execute_action.side_effect = TimeoutError()
    first = await execute(service, remediation, scope)
    assert first["status"] == "unknown"
    assert repo.remediations[remediation.id].recheck_run_id is None
    actuator.get_operation.return_value = {"state": "unknown", "action_outcome": "failed"}
    second = await execute(service, remediation, scope)
    assert second["status"] == "unknown"
    actuator.get_operation.return_value = operation_envelope(remediation)
    third = await execute(service, remediation, scope)
    assert third["status"] == "executed"
    await execute(service, remediation, scope)
    actuator.execute_action.assert_awaited_once()
    assert actuator.get_operation.await_count == 2
    assert actuator.get_operation.await_args.args[1] == remediation.id
    service._patrol_run_service.trigger_recheck.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["applied", "succeeded", "failed", "skipped_idempotent", None])
async def test_outcome_label_without_bound_receipt_never_proves_effect(outcome):
    service, _repo, remediation, actuator, scope = await proposed()
    actuator.execute_action.return_value = {"action_outcome": outcome}
    assert (await execute(service, remediation, scope))["status"] == "unknown"
    service._patrol_run_service.trigger_recheck.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["uid", "operation", "digest", "evidence", "expiry"])
async def test_receipt_binding_or_evidence_mismatch_stays_unknown(broken):
    service, _repo, remediation, actuator, scope = await proposed()
    envelope = deepcopy(operation_envelope(remediation))
    if broken == "uid":
        envelope["receipt"]["target"]["uid"] = "recreated"
    elif broken == "operation":
        envelope["operation_id"] = "other-operation"
    elif broken == "digest":
        envelope["receipt"]["payload_digest"] = "0" * 64
    elif broken == "evidence":
        envelope["evidence"][0]["sha256"] = "0" * 64
    else:
        envelope["evidence"][0]["expires_at"] = "2020-01-01T00:00:00+00:00"
    actuator.execute_action.return_value = envelope
    result = await execute(service, remediation, scope)
    assert result["status"] == "unknown"
    assert result["error_code"] == "ACTUATOR_RECEIPT_INVALID"
    service._patrol_run_service.trigger_recheck.assert_not_awaited()


@pytest.mark.asyncio
async def test_not_applied_receipt_is_terminal_and_new_proposal_gets_new_key():
    service, repo, remediation, actuator, scope = await proposed()
    actuator.execute_action.return_value = operation_envelope(remediation, "not_applied")
    assert (await execute(service, remediation, scope))["status"] == "failed"
    service._patrol_run_service.trigger_recheck.assert_not_awaited()
    new = await service.propose(
        repo.k8s_finding.id,
        PatrolRemediationAction.SCALE_WORKLOAD,
        {"replicas": 2},
        scope,
        "user-1",
    )
    assert new.actuator_operation_id != remediation.actuator_operation_id
    assert new.idempotency_key != remediation.idempotency_key
    actuator.execute_action.assert_awaited_once()


@pytest.mark.asyncio
async def test_applied_without_result_material_waits_and_does_not_recheck():
    service, _repo, remediation, actuator, scope = await proposed()
    envelope = operation_envelope(remediation)
    envelope["receipt"]["after"] = None
    envelope["evidence"][0]["sha256"] = hashlib.sha256(
        json.dumps(
            envelope["receipt"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    actuator.execute_action.return_value = envelope
    result = await execute(service, remediation, scope)
    assert result["status"] == "unknown"
    assert result["actuator_receipt"]["state"] == "applied"
    assert result["error_code"] == "ACTUATOR_RESULT_MATERIAL_MISSING"
    service._patrol_run_service.trigger_recheck.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_after_session_termination_still_blocks_competing_proposal():
    service, repo, remediation, _actuator, scope = await proposed()
    remediation.status = PatrolRemediationStatus.EXECUTING
    await service.cancel_if_pending(remediation.session_id)
    assert remediation.status is PatrolRemediationStatus.UNKNOWN
    with pytest.raises(ConflictError):
        await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.SCALE_WORKLOAD,
            {"replicas": 2},
            scope,
            "user-1",
        )


@pytest.mark.asyncio
async def test_legacy_capability_cannot_admit_new_write():
    actuator = SimpleNamespace(
        get_capabilities=AsyncMock(return_value={"overall_capability_hash": "legacy"})
    )
    with pytest.raises(ConflictError):
        await proposed(actuator=actuator)


@pytest.mark.asyncio
async def test_target_substitution_is_rejected_before_approval():
    actuator = SimpleNamespace(
        get_capabilities=AsyncMock(return_value=capabilities()),
        get_target=AsyncMock(return_value={"schema_version": 2, "uid": "invented"}),
    )
    with pytest.raises(ConflictError):
        await proposed(actuator=actuator)


@pytest.mark.asyncio
async def test_unresolved_namespace_probe_needs_explicit_workload():
    repo = Repo()
    del repo.run.pack_snapshot["config"]["checks"][0]["probe"]["args"]["workload"]
    service = make_service(Uow(repo))
    with pytest.raises(BadRequestError):
        await service.propose(
            repo.k8s_finding.id,
            PatrolRemediationAction.RESTART_WORKLOAD,
            {},
            OwnerScope.personal("user-1"),
            "user-1",
        )
