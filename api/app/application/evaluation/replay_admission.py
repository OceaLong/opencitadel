"""E06 pre-admission seam. Persist this binding before enqueueing CreateRun."""

from app.application.evaluation.preflight import validate_current_config
from app.application.evaluation.recording_authority import validate_recording
from app.domain.evaluation.configuration import ConfigVersion
from app.domain.evaluation.errors import ReplayMismatch
from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot

RECORDED_SOURCE_TYPE = "evaluation_recorded_case"


async def prepare_replay_binding(
    uow,
    suites,
    scope,
    principal,
    *,
    run_id,
    source_entity_id,
    config_version_id,
    recording_version_id,
    policy_pair,
    policy_snapshot,
):
    """Borrow E06's kernel-login business UoW under the original principal.

    The returned source marker and run ID MUST be used by admission. Enqueue its
    CreateRun command via the same UoW command_sink; alternatively commit this
    binding first. Never expose a dispatchable ordinary Run before binding.
    E06 separately owns fresh suite/budget admission. This function never commits,
    constructs a provider, admits a Run or borrows another DB connection.
    """
    if not source_entity_id or len(source_entity_id) > 255:
        raise ValueError("invalid_evaluation_source")
    await uow.evaluation_dataset.authorize(scope, principal, write=True)
    config = ConfigVersion.model_validate(
        await uow.evaluation_configuration.get_version(scope, "config", config_version_id)
    )
    if (
        config.selection.purpose != "evaluation_subject"
        or policy_snapshot.family.value != config.selection.mode
    ):
        raise ReplayMismatch("replay_configuration_mismatch")
    if derive_run_policy_snapshot(policy_pair.execution, policy_snapshot.family) != policy_snapshot:
        raise ReplayMismatch("replay_admission_policy_changed")
    current = await suites.resolved(
        uow, scope, config.selection, principal, policy_pair=policy_pair
    )
    if validate_current_config(config, current):
        raise ReplayMismatch("replay_configuration_changed")
    manifest = await validate_recording(uow, scope, principal, recording_version_id)
    contracts = {c.name: c for c in manifest.contracts}
    for expected in config.snapshot["contracts"]:
        actual = contracts.get(expected["name"])
        if (
            actual is None
            or actual.schema_body != expected["schema"]
            or actual.policy.model_dump(mode="json") != expected["policy"]
        ):
            raise ReplayMismatch("replay_configuration_contract_changed")
    admission = {
        "config_version_id": str(config.id),
        "config_fingerprint": config.fingerprint,
        "policy_digest": policy_snapshot.snapshot_digest,
        "source_entity_id": source_entity_id,
        "source_entity_type": RECORDED_SOURCE_TYPE,
        "purpose": "evaluation_subject",
        "tool_names": list(config.selection.tool_names),
    }
    if await uow.evaluation_recording.binding(scope, run_id) is None:
        await uow.evaluation_recording.require_unadmitted(scope, run_id)
    await uow.evaluation_recording.bind(
        scope, run_id, recording_version_id, principal, admission=admission
    )
    return {
        "run_id": run_id,
        "source_entity_id": source_entity_id,
        "source_entity_type": RECORDED_SOURCE_TYPE,
        "usage_purpose": "evaluation_subject",
    }
