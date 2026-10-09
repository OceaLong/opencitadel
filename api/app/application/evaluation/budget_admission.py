"""Kernel-owned budget preadmission, borrowing E06's business transaction.

Control rows are authority for fixed scope/budget/candidate identities, not a Run
admission or scheduler. E06 must keep source prebinding and CreateRun atomic.
"""

from decimal import Decimal

from app.application.evaluation.preflight import validate_current_config
from app.domain.evaluation.budget_binding import BudgetNamespace, BudgetRunBinding
from app.domain.evaluation.configuration import (
    ConfigVersion,
    SuiteVersion,
    dataset_membership_digest,
)
from app.domain.evaluation.rubric import RubricVersion
from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot


async def prepare_budget_namespace(
    uow, suites, scope, principal, *, namespace_id, suite_version_id, policy_pair
):
    """Lock namespace before E03/E04 source prebinding, then bind/enqueue in this UoW.

    Re-delivery recomputes current proof but never changes original batch limits or
    requester. No dataset body read is authorized by this metadata check.
    """
    await uow.evaluation_dataset.authorize(scope, principal, write=True)
    repo = uow.evaluation_configuration
    suite = SuiteVersion.model_validate(await repo.get_version(scope, "suite", suite_version_id))
    suite.settings.validate_limits(suites.limits())
    dataset = await uow.evaluation_dataset.get_version(scope, suite.dataset_version)
    if (
        dataset["revision"] != suite.dataset_proof.revision
        or dataset_membership_digest(dataset) != suite.dataset_proof.membership_digest
    ):
        raise ValueError("budget_dataset_changed")
    pins = await uow.resource_pins.validate(
        scope, "dataset_version", str(suite.dataset_version), suite.dataset_proof.resources
    )
    if any(not pin.available for pin in pins):
        raise ValueError("budget_dataset_unavailable")
    rubric = RubricVersion.model_validate(
        await repo.get_version(scope, "rubric", suite.rubric_version)
    )
    configs = []
    for identity in (*suite.config_versions, rubric.judge_config_version):
        config = ConfigVersion.model_validate(await repo.get_version(scope, "config", identity))
        current = await suites.resolved(
            uow, scope, config.selection, principal, policy_pair=policy_pair
        )
        if validate_current_config(config, current):
            raise ValueError("budget_configuration_changed")
        configs.append(config)
    if suites.budgets is None:
        raise ValueError("budget_authority_unavailable")
    evidence = await suites.budgets.check_in_uow(
        scope, suite, tuple(configs), for_start=True, uow=uow, principal=principal
    )
    if not evidence.ready:
        raise ValueError("budget_preadmission_unavailable")
    value = BudgetNamespace(
        id=namespace_id,
        suite_version_id=suite.id,
        suite_fingerprint=suite.fingerprint,
        requester=principal.model_dump(mode="json"),
        token_budget=suite.settings.token_budget,
        money_budget=Decimal(str(suite.settings.money_budget))
        if suite.settings.money_budget is not None
        else None,
        case_ids=tuple(member["id"] for member in dataset["members"]),
        config_versions=suite.config_versions,
        judge_config_version=rubric.judge_config_version,
        repeat=suite.settings.repeat,
        mode=suite.mode,
        recording_versions=suite.recording_versions,
        environment_version=suite.environment_version,
        policy_revision=str(policy_pair.execution.revision.id),
        operations_revision=str(policy_pair.operations.revision.id),
        inventory=suites.budgets.inventory.fingerprint,
        config_fingerprints={str(config.id): config.fingerprint for config in configs},
    )
    saved = await uow.evaluation_budget_control.create(scope, value)
    await uow.evaluation_dataset.authorize(scope, principal, write=True)
    if saved.state != "open":
        raise ValueError("budget_namespace_closed")
    return saved


async def prepare_budget_binding(
    uow, suites, scope, principal, *, selection, policy_pair, policy_snapshot
):
    """Call after prepare_budget_namespace and E03/E04 source prebinding, before enqueue.

    Namespace lock is retained through source preparation, binding and CreateRun.
    E06 must rollback the whole UoW on any error, including source preparation.
    """
    repo = uow.evaluation_budget_control
    namespace = await repo.namespace(scope, selection.namespace_id, lock=True)
    await uow.evaluation_dataset.authorize(scope, principal, write=True)
    if namespace.state != "open":
        raise ValueError("budget_namespace_closed")
    if namespace.requester != principal.model_dump(mode="json"):
        raise ValueError("budget_original_requester_required")
    if (
        selection.case_id not in namespace.case_ids
        or selection.subject_config_version_id not in namespace.config_versions
        or selection.repeat > namespace.repeat
    ):
        raise ValueError("budget_case_membership_invalid")
    config = ConfigVersion.model_validate(
        await uow.evaluation_configuration.get_version(scope, "config", selection.config_version_id)
    )
    purpose = config.selection.purpose
    expected_config = (
        namespace.judge_config_version
        if purpose == "evaluation_judge"
        else selection.subject_config_version_id
    )
    if (
        config.id != expected_config
        or namespace.config_fingerprints.get(str(config.id)) != config.fingerprint
    ):
        raise ValueError("budget_config_membership_invalid")
    if (
        namespace.policy_revision != str(policy_pair.execution.revision.id)
        or namespace.operations_revision != str(policy_pair.operations.revision.id)
        or config.selection.mode != policy_snapshot.family.value
        or derive_run_policy_snapshot(policy_pair.execution, policy_snapshot.family)
        != policy_snapshot
    ):
        raise ValueError("budget_admission_policy_changed")
    current = await suites.resolved(
        uow, scope, config.selection, principal, policy_pair=policy_pair
    )
    if (
        validate_current_config(config, current)
        or current.get("budget", {}).get("inventory") != namespace.inventory
    ):
        raise ValueError("budget_configuration_changed")
    source_type = "evaluation_judge"
    if purpose == "evaluation_subject":
        source = await (
            uow.evaluation_recording.binding(scope, selection.run_id)
            if namespace.mode == "recorded"
            else uow.evaluation_environment.binding(scope, selection.run_id)
        )
        source_type = (
            "evaluation_recorded_case"
            if namespace.mode == "recorded"
            else "evaluation_isolated_case"
        )
        expected = {
            "config_version_id": str(config.id),
            "config_fingerprint": config.fingerprint,
            "policy_digest": policy_snapshot.snapshot_digest,
            "source_entity_id": selection.source_entity_id,
            "source_entity_type": source_type,
            "purpose": purpose,
        }
        if (
            source is None
            or source["principal"] != namespace.requester
            or any(source["admission"].get(key) != value for key, value in expected.items())
        ):
            raise ValueError("budget_source_binding_mismatch")
        if namespace.mode == "recorded":
            if source["version_id"] not in namespace.recording_versions:
                raise ValueError("budget_recording_membership_invalid")
        else:
            lease = await uow.evaluation_environment.lease(scope, source["lease_id"])
            slot = lease.case_slot
            workspace = "team:" + scope.team_id if scope.team_id else "user:" + principal.user_id
            if (
                lease.state != "leased"
                or lease.requester != namespace.requester
                or slot.workspace != workspace
                or lease.environment_version != namespace.environment_version
                or lease.generation != source["generation"]
                or slot.batch_id != namespace.id
                or slot.case_id != selection.case_id
                or slot.config_version != config.id
                or slot.repeat != selection.repeat
            ):
                raise ValueError("budget_environment_membership_invalid")
    previous = await repo.binding(scope, selection.run_id)
    if previous is None:
        await uow.evaluation_recording.require_unadmitted(scope, selection.run_id)
    value = BudgetRunBinding(
        **selection.model_dump(),
        purpose=purpose,
        source_entity_type=source_type,
        requester=namespace.requester,
        config_fingerprint=config.fingerprint,
        candidate_proof=current["budget"],
        policy_digest=policy_snapshot.snapshot_digest,
        policy_revision=namespace.policy_revision,
        operations_revision=namespace.operations_revision,
    )
    result = await repo.bind(scope, value)
    await uow.evaluation_dataset.authorize(scope, principal, write=True)
    return result
