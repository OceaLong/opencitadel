"""Kernel-only pre-admission binding. E06 owns batch admission and CreateRun enqueue."""

from datetime import UTC, datetime

from app.application.evaluation.environment_service import validate_environment
from app.application.evaluation.preflight import validate_current_config
from app.domain.evaluation.configuration import ConfigVersion
from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot

ISOLATED_SOURCE_TYPE = "evaluation_isolated_case"


async def prepare_environment_binding(
    uow,
    suites,
    scope,
    principal,
    *,
    run_id,
    source_entity_id,
    config_version_id,
    lease_id,
    policy_pair,
    policy_snapshot,
    registry,
    ceiling=2,
):
    await uow.evaluation_dataset.authorize(scope, principal, write=True)
    config = ConfigVersion.model_validate(
        await uow.evaluation_configuration.get_version(scope, "config", config_version_id)
    )
    if (
        not source_entity_id
        or len(source_entity_id) > 255
        or config.selection.purpose != "evaluation_subject"
        or config.selection.mode != policy_snapshot.family.value
    ):
        raise ValueError("environment_admission_configuration_invalid")
    if derive_run_policy_snapshot(policy_pair.execution, policy_snapshot.family) != policy_snapshot:
        raise ValueError("environment_admission_policy_changed")
    current = await suites.resolved(
        uow, scope, config.selection, principal, policy_pair=policy_pair
    )
    if validate_current_config(config, current):
        raise ValueError("environment_admission_configuration_changed")
    repo = uow.evaluation_environment
    lease = await repo.lease(scope, lease_id, lock=True)
    if (
        lease.state != "ready"
        or lease.expires_at <= datetime.now(UTC)
        or lease.case_slot.config_version != config.id
    ):
        raise ValueError("environment_lease_not_ready")
    value = await repo.registered(scope, "environment", lease.environment_version)
    adapter, targets, _ = await validate_environment(
        uow, scope, principal, value, registry, ceiling=ceiling
    )
    registry.validate_tools(adapter, config, targets, environment_id=value.id)
    await uow.evaluation_recording.require_unadmitted(scope, run_id)
    admission = {
        "source_entity_type": ISOLATED_SOURCE_TYPE,
        "source_entity_id": source_entity_id,
        "purpose": "evaluation_subject",
        "policy_digest": policy_snapshot.snapshot_digest,
        "config_version_id": str(config.id),
        "config_fingerprint": config.fingerprint,
        "tool_names": list(config.selection.tool_names),
        "contracts": list(config.snapshot["contracts"]),
    }
    from app.domain.evaluation.environment import transition

    leased = transition(lease, "leased")
    await repo.save(scope, lease, leased)
    await repo.bind(scope, run_id, leased, principal, admission)
    return {
        "run_id": run_id,
        "source_entity_type": ISOLATED_SOURCE_TYPE,
        "source_entity_id": source_entity_id,
        "usage_purpose": "evaluation_subject",
    }
