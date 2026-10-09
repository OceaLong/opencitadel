"""Executable durable model-scoring consumer; provider work stays in restricted ASK."""

import logging
from uuid import NAMESPACE_URL, uuid5

from app.application.evaluation.budget_admission import (
    prepare_budget_binding,
    prepare_budget_namespace,
)
from app.application.evaluation.configuration_bridge import f07_configuration_evidence
from app.domain.evaluation.budget_binding import BudgetBindingSelection
from app.domain.evaluation.execution_slots import ExecutionCapacityUnavailable
from app.domain.evaluation.judge_protocol import JudgeAdmission
from app.domain.evaluation.rule_engine import MISSING
from app.domain.evaluation.scoring import ScoringProjectionAdvanced
from app.domain.execution.family import RunFamily
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import Principal
from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot

KERNEL = AuthorizationContext.system("execution-kernel")
logger = logging.getLogger(__name__)


def authority_reason(error):
    # Only emit fixed diagnostic codes, never private materials or exception text.
    reasons = {
        "judge_binding_unavailable",
        "judge_cancelled",
        "judge_policy_changed",
        "judge_protocol_unavailable",
        "judge_effect_unknown",
        "judge_effect_unresolved",
        "scoring_candidate_stale",
        "scoring_execution_unavailable",
        "scoring_original_requester_required",
        "judge_material_unavailable",
        "judge_recording_changed",
    }
    return str(error) if str(error) in reasons else "authority_unavailable"


class JudgeService:
    def __init__(self, uow_factory, suites, evidence, admission, *, execution_policy):
        self.uow_factory, self.suites, self.evidence, self.admission = (
            uow_factory,
            suites,
            evidence,
            admission,
        )
        self.execution_policy = execution_policy

    async def schedule(self, scope, result_revision, rubric_version, request_id):
        candidate = result_revision
        async with self.uow_factory(KERNEL) as work:
            batch = await work.evaluation_batch.get(scope, candidate.batch_id)
            principal = Principal.model_validate(batch["principal"])
            await work.evaluation_dataset.authorize(scope, principal, write=True)
            identity = uuid5(NAMESPACE_URL, f"judge:{scope}:{candidate.result_id}:{request_id}")
            prior = await work.evaluation_judge.get(scope, uuid5(identity, "run"))
            if prior:
                if prior["rubric_id"] != rubric_version or prior[
                    "candidate"
                ] != candidate.model_dump(mode="json"):
                    raise ValueError("judge_request_conflict")
                if prior["status"] != "pending":
                    return prior["run_id"]
                intent = prior
            else:
                await work.evaluation_score.eligible(scope, principal, candidate)
                intent = None
        suite = await self.suites.get_version(scope, principal, "suite", candidate.suite_version_id)
        if rubric_version != suite.rubric_version:
            raise ValueError("judge_rescore_request_required")
        rubric = await self.suites.get_version(scope, principal, "rubric", rubric_version)
        config = await self.suites.get_version(
            scope, principal, "config", rubric.judge_config_version
        )
        if intent is None:
            dataset = await self.suites.datasets.get_version(
                scope, principal, suite.dataset_version
            )
            case = next(c for c in dataset.cases if c.id == candidate.case_revision_id)
            evidence = await self.evidence.read(
                scope, principal, candidate, resources=tuple(case.resources)
            )
            materials = judge_materials(case, rubric, evidence)
            async with self.uow_factory(KERNEL) as work:
                intent = await work.evaluation_judge.create(
                    scope,
                    principal,
                    candidate,
                    rubric_id=rubric.id,
                    config_id=config.id,
                    request_id=request_id,
                    materials=materials,
                    namespace_id=candidate.batch_id,
                )
                await work.commit()
        await self._admit(scope, intent, suite, config)
        return intent["run_id"]

    async def rescore(self, scope, candidate, request, request_id, *, principal):
        from app.domain.evaluation.judge_protocol import RescoreRequest

        request = RescoreRequest.model_validate(request)
        authorizer = principal
        async with self.uow_factory(KERNEL) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=True)
            batch = await work.evaluation_batch.get(scope, candidate.batch_id)
            principal = Principal.model_validate(batch["principal"])
            scope = scope.model_copy(update={"user_id": principal.user_id})
            await work.evaluation_dataset.authorize(scope, principal, write=True)
            identity = uuid5(NAMESPACE_URL, f"judge:{scope}:{candidate.result_id}:{request_id}")
            prior = await work.evaluation_judge.get(scope, uuid5(identity, "run"))
            if prior and (
                prior["authorizer"] != authorizer.model_dump(mode="json")
                or prior["rescore"] != request.model_dump(mode="json")
                or prior["candidate"] != candidate.model_dump(mode="json")
            ):
                raise ValueError("judge_request_conflict")
            if prior and prior["status"] != "pending":
                return prior["run_id"]
        suite = await self.suites.get_version(scope, principal, "suite", candidate.suite_version_id)
        rubric = await self.suites.get_version(scope, principal, "rubric", request.rubric_version)
        if rubric.judge_config_version != request.judge_config_version:
            raise ValueError("judge_configuration_mismatch")
        config = await self.suites.get_version(
            scope, principal, "config", request.judge_config_version
        )
        if prior is None:
            dataset = await self.suites.datasets.get_version(
                scope, principal, suite.dataset_version
            )
            case = next(c for c in dataset.cases if c.id == candidate.case_revision_id)
            evidence = await self.evidence.read(
                scope, principal, candidate, resources=tuple(case.resources)
            )
            materials = judge_materials(case, rubric, evidence)
            async with self.uow_factory(KERNEL) as work:
                prior = await work.evaluation_judge.create(
                    scope,
                    principal,
                    candidate,
                    rubric_id=rubric.id,
                    config_id=config.id,
                    request_id=request_id,
                    materials=materials,
                    namespace_id=uuid5(identity, "additional-budget"),
                    rescore=request.model_dump(mode="json"),
                    authorizer=authorizer,
                )
                await work.commit()
        await self._admit(scope, prior, suite, config)
        return prior["run_id"]

    async def _admit(self, scope, intent, suite, config):
        from app.domain.evaluation.batch import ScoringCandidate

        candidate = ScoringCandidate.model_validate(intent["candidate"])
        pair = await self.suites.policies.load_active_pair()
        snapshot = derive_run_policy_snapshot(pair.execution, RunFamily.ASK)
        service = self
        async with self.uow_factory(KERNEL) as work:
            _, principal = await work.evaluation_judge.current(scope, intent)
            subject = await work.evaluation_budget_control.binding(scope, candidate.run_id)

        async def resolver(owner, run_id, actual_policy):
            if actual_policy != pair.execution.revision.policy:
                raise ValueError("judge_policy_changed")
            evidence = f07_configuration_evidence(
                scope, config, pair.execution, purpose="evaluation_judge"
            )
            authorization = AuthorizationContext.for_principal(principal, scope=scope)
            async with service.uow_factory(authorization) as work:
                await work.evaluation_dataset.authorize(scope, principal, write=True)
                evidence["physical_requester"] = await work.execution_usage.capture_requester(
                    scope, authorization, run_id=run_id
                )
                saved = await work.execution_usage.admission_snapshot(
                    scope, run_id, evidence, "evaluation_judge"
                )
                await work.commit()
                return saved

        class Sink:
            async def receive(self, envelope, *, max_active_runs=0):
                if envelope.payload["policy_snapshot"] != snapshot.model_dump(mode="json"):
                    raise ValueError("judge_policy_changed")
                async with service.uow_factory(KERNEL) as work:
                    await work.evaluation_batch.lock(scope, candidate.batch_id)
                    saved = await work.evaluation_judge.get(scope, intent["run_id"])
                    if saved["status"] != "pending":
                        return None
                    await work.evaluation_judge.current(scope, intent, lock=True)
                    if intent["rescore"]:
                        await prepare_rescore_namespace(
                            work,
                            service.suites,
                            scope,
                            principal,
                            intent,
                            suite,
                            config,
                            pair,
                            subject,
                        )
                    else:
                        await prepare_budget_namespace(
                            work,
                            service.suites,
                            scope,
                            principal,
                            namespace_id=intent["namespace_id"],
                            suite_version_id=suite.id,
                            policy_pair=pair,
                        )
                    await prepare_budget_binding(
                        work,
                        service.suites,
                        scope,
                        principal,
                        selection=BudgetBindingSelection(
                            namespace_id=intent["namespace_id"],
                            run_id=intent["run_id"],
                            source_entity_id=str(intent["id"]),
                            case_id=candidate.case_revision_id,
                            config_version_id=config.id,
                            subject_config_version_id=candidate.config_version_id,
                            repeat=subject.repeat,
                        ),
                        policy_pair=pair,
                        policy_snapshot=snapshot,
                    )
                    await work.evaluation_execution.prepare(
                        scope, intent["run_id"], service.execution_policy
                    )
                    received = await work.execution_commands.receive(
                        envelope, max_active_runs=max_active_runs
                    )
                    await work.evaluation_judge.update(
                        scope, intent, status="submitted", envelope=envelope
                    )
                    await work.commit()
                    return received

        await self.admission.admit(
            family=RunFamily.ASK,
            source_entity_type="evaluation_judge",
            source_entity_id=str(intent["id"]),
            owner_scope=scope,
            private_input={
                "message": "Evaluate the fixed authorized materials.",
                "model_id": config.selection.model_id,
            },
            public_input={},
            run_id=intent["run_id"],
            parent_run_id=candidate.run_id,
            idempotency_key=f"judge:{intent['id']}",
            usage_purpose="evaluation_judge",
            configuration_resolver=resolver,
            command_sink=Sink(),
            judge_admission=JudgeAdmission(intent["run_id"], intent["id"]),
        )

    async def tick(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_judge_limit")
        async with self.uow_factory(KERNEL) as work:
            batches = await work.evaluation_judge.ready_batches(limit=limit)
        settled = 0
        for scope, batch_id in batches:
            settled += await self.reconcile_batch(scope, batch_id, limit=limit)
            async with self.uow_factory(KERNEL) as work:
                await work.evaluation_judge.checked(scope, batch_id)
                await work.commit()
        return settled

    async def score_batch(self, scope, batch_id, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_judge_limit")
        async with self.uow_factory(KERNEL) as work:
            candidates = await work.evaluation_batch.scoring_candidates(scope, batch_id, limit=5000)
            pending = []
            for candidate in candidates:
                if await work.evaluation_score.settled(scope, candidate.result_id, "model") is None:
                    pending.append(candidate)
                    if len(pending) >= limit:
                        break
        count = 0
        for candidate in pending:
            async with self.uow_factory(KERNEL) as work:
                batch = await work.evaluation_batch.get(scope, batch_id)
                principal = Principal.model_validate(batch["principal"])
            suite = await self.suites.get_version(
                scope, principal, "suite", candidate.suite_version_id
            )
            try:
                await self.schedule(
                    scope,
                    candidate,
                    suite.rubric_version,
                    f"model:{candidate.result_id}:{candidate.run_revision}",
                )
                count += 1
            except ExecutionCapacityUnavailable:
                # Admission left the intent pending. A later scoring tick retries
                # it after capacity frees up; do not fail the critical kernel lane.
                break
            except ScoringProjectionAdvanced:
                # Re-read the completed subject cut on the next automatic tick.
                continue
            except ValueError as error:
                if str(error) not in {
                    "scoring_candidate_stale",
                    "judge_request_conflict",
                    "judge_already_scheduled",
                    "scoring_effect_unresolved",
                }:
                    raise
        return count

    async def cancel(self, scope, principal, run_id, *, review_command_id=None):
        """Cancel one scoring authorization, including a rescore of a terminal batch."""
        from datetime import UTC, datetime

        async with self.uow_factory(KERNEL) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=True)
            intent = await work.evaluation_judge.get(scope, run_id)
            if intent is None:
                raise ValueError("judge_not_found")
            await work.evaluation_batch.lock(scope, intent["batch_id"])
            if review_command_id is not None:
                batch = await work.evaluation_batch.get(scope, intent["batch_id"])
                original = Principal.model_validate(batch["principal"])
                await work.evaluation_dataset.authorize(
                    scope.model_copy(update={"user_id": original.user_id}), original, write=True
                )
                await work.evaluation_review.validate_cancellation(
                    scope, review_command_id, run_id, principal
                )
            await work.evaluation_judge.cancel(
                scope, intent["batch_id"], self.execution_policy, datetime.now(UTC), run_id=run_id
            )
            if review_command_id is not None:
                await work.evaluation_review.bind_cancellation(
                    scope, review_command_id, run_id, principal
                )
            await work.commit()

    async def reconcile_batch(self, scope, batch_id, *, limit=100):
        from datetime import UTC, datetime

        from app.domain.evaluation.judge_protocol import validate_output
        from app.domain.evaluation.scoring import RecordingEvidence, ScoreValue
        from app.domain.execution.commands import CommandEnvelope
        from app.domain.models.resource_pin import ResourceIdentity

        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_judge_limit")
        async with self.uow_factory(KERNEL) as work:
            await work.evaluation_batch.lock(scope, batch_id)
            batch = await work.evaluation_batch.get(scope, batch_id)
            await work.evaluation_judge.observe_unknown(scope, batch_id)
            if (
                await work.evaluation_batch.cancellation_requested(scope, batch_id)
                or batch["status"] == "cancelling"
            ):
                await work.evaluation_judge.cancel(
                    scope, batch_id, self.execution_policy, datetime.now(UTC)
                )
                await work.commit()
                return 0
            active = await work.evaluation_judge.active(scope, batch_id)
            requirements = await work.evaluation_review.requirements(scope, batch_id)
            await work.commit()
        requirements_unavailable = False
        if active and requirements is None:
            from app.domain.evaluation.review import case_review_requirements

            try:
                original = Principal.model_validate(batch["principal"])
                original_scope = scope.model_copy(update={"user_id": original.user_id})
                suite = await self.suites.get_version(
                    original_scope, original, "suite", batch["suite_version"]
                )
                rubric = await self.suites.get_version(
                    original_scope, original, "rubric", suite.rubric_version
                )
                dataset = await self.suites.datasets.get_version(
                    original_scope, original, suite.dataset_version
                )
                async with self.uow_factory(KERNEL) as work:
                    await work.evaluation_review.requirements(
                        scope, batch_id, case_review_requirements(dataset, rubric)
                    )
                    await work.commit()
            except (ValueError, PermissionError):
                # Missing immutable facts prohibit scoring, not cleanup. A revoked
                # source must not strand legacy pending/admitted judge work here.
                requirements_unavailable = True
        count = 0
        for intent in active[:limit]:
            if requirements_unavailable:
                async with self.uow_factory(KERNEL) as work:
                    await work.evaluation_batch.lock(scope, batch_id)
                    await work.evaluation_judge.cancel(
                        scope,
                        batch_id,
                        self.execution_policy,
                        datetime.now(UTC),
                        run_id=intent["run_id"],
                    )
                    await work.commit()
                continue
            if intent["status"] == "pending":
                from app.domain.evaluation.execution_slots import ExecutionCapacityUnavailable

                try:
                    async with self.uow_factory(KERNEL) as work:
                        _, principal = await work.evaluation_judge.current(scope, intent)
                    suite = await self.suites.get_version(
                        scope, principal, "suite", intent["candidate"]["suite_version_id"]
                    )
                    config = await self.suites.get_version(
                        scope, principal, "config", intent["config_id"]
                    )
                    await self._admit(scope, intent, suite, config)
                except ExecutionCapacityUnavailable:
                    continue
                except (ValueError, PermissionError) as error:
                    if str(error) == "scoring_effect_unresolved":
                        continue
                    async with self.uow_factory(KERNEL) as work:
                        await work.evaluation_batch.lock(scope, batch_id)
                        saved = await work.evaluation_judge.get(scope, intent["run_id"])
                        if saved["status"] == "pending":
                            await work.evaluation_judge.stop_unscored(
                                scope, saved, error="judge_admission_unavailable"
                            )
                        await work.commit()
                continue
            async with self.uow_factory(KERNEL) as work:
                await work.evaluation_batch.lock(scope, batch_id)
                saved = await work.evaluation_judge.get(scope, intent["run_id"])
                if saved["status"] != "submitted":
                    continue
                envelope = CommandEnvelope.model_validate(saved["envelope"])
                receipt = await work.evaluation_batch.receipt(
                    scope, {"run_id": intent["run_id"], "command_id": envelope.command_id}
                )
                projection = await work.evaluation_judge.projection(scope, intent)
                if not receipt or receipt["status"] not in {
                    "accepted",
                    "rejected",
                    "dead_lettered",
                }:
                    continue
                if receipt["status"] == "accepted" and (
                    not projection or not projection["terminal"]
                ):
                    try:
                        await work.evaluation_judge.authorize_run(scope, intent["run_id"])
                    except (ValueError, PermissionError) as error:
                        if str(error) in {"scoring_effect_unresolved", "judge_effect_unknown"}:
                            # Unknown physical effects can precede the formal failed
                            # projection. Do not create a cancellation that would
                            # hide its later null diagnostic; keep all budget holds.
                            continue
                        logger.warning(
                            "Judge authority cancelled run=%s reason=%s",
                            intent["run_id"],
                            authority_reason(error),
                        )
                        await work.evaluation_judge.cancel(
                            scope,
                            batch_id,
                            self.execution_policy,
                            datetime.now(UTC),
                            run_id=intent["run_id"],
                        )
                        await work.commit()
                    continue
                if receipt["status"] != "accepted":
                    await work.evaluation_execution.withdraw_unaccepted(
                        scope, intent["run_id"], envelope.command_id, self.execution_policy
                    )
                if saved["cancel_envelope"]:
                    await work.evaluation_judge.stop_unscored(scope, saved, error="judge_cancelled")
                    await work.commit()
                    continue
                candidate = principal = None
                unknown_failure = False
                try:
                    candidate, principal = await work.evaluation_judge.current(
                        scope, intent, lock=True
                    )
                    await work.evaluation_judge.authorize_run(scope, intent["run_id"])
                except (ValueError, PermissionError) as error:
                    if str(error) == "scoring_effect_unresolved":
                        continue
                    if (
                        str(error) == "judge_effect_unknown"
                        and candidate is not None
                        and principal is not None
                        and receipt["status"] == "accepted"
                        and projection["terminal"]
                        and projection["status"] == "failed"
                        and projection["state"].get("failure_code")
                        == "NON_IDEMPOTENT_OUTCOME_UNKNOWN"
                    ):
                        # Retain a null diagnostic for this actual failed Judge.
                        # Its immutable unknown facts and held budget remain intact.
                        unknown_failure = True
                    else:
                        await work.evaluation_judge.stop_unscored(
                            scope, intent, error="judge_authority_unavailable"
                        )
                        await work.commit()
                        continue
                error = "judge_execution_failed"
                output = None
                if (
                    receipt["status"] == "accepted"
                    and projection["status"] == "failed"
                    and projection["state"].get("failure_code") == "JUDGE_OUTPUT_INVALID"
                ):
                    # The native restricted protocol exhausted its bounded invalid
                    # output rounds. Preserve that cause instead of a transport error.
                    error = "judge_output_invalid"
                if (
                    receipt["status"] == "accepted"
                    and await work.evaluation_judge.unsafe(scope, intent)
                    and not unknown_failure
                ):
                    # A normal late settlement can still validate this exact result.
                    continue
                if receipt["status"] == "accepted" and projection["status"] == "completed":
                    try:
                        text = await work.evaluation_judge.output(scope, intent, projection)
                        output = validate_output(text, intent["materials"])
                    except ValueError:
                        error = "judge_output_invalid"
                resources = tuple(
                    ResourceIdentity.model_validate(r)
                    for r in intent["materials"].get("resources", [])
                )
                recording = (
                    RecordingEvidence.model_validate(intent["materials"]["recording"])
                    if intent["materials"].get("recording")
                    else None
                )
                scores = (
                    [
                        ScoreValue(
                            source="model",
                            dimension=d.name,
                            rubric_revision=intent["rubric_id"],
                            value=d.score,
                            reason=d.reason,
                            evidence=tuple(
                                ResourceIdentity.model_validate(
                                    intent["materials"]["evidence"][key]["resource"]
                                )
                                for key in d.evidence
                            ),
                            recording=recording,
                            status="valid" if d.score is not None else "not_evaluable",
                        )
                        for d in output.dimensions
                    ]
                    if output
                    else [
                        ScoreValue(
                            source="model",
                            dimension=d["id"],
                            rubric_revision=intent["rubric_id"],
                            value=None,
                            reason=error,
                            evidence=resources,
                            recording=recording,
                            status="error",
                        )
                        for d in intent["materials"]["rubric"]
                    ]
                )
                revision = await work.evaluation_score.revision(scope, batch_id)
                revision = await work.evaluation_score.append(
                    scope,
                    principal,
                    candidate,
                    source="model",
                    scores=scores,
                    expected_evaluation_revision=revision,
                    request_id="judge:" + str(intent["id"]),
                    required_dimensions=(),
                    applicable_dimensions=tuple(d["id"] for d in intent["materials"]["rubric"]),
                    judge_run_id=intent["run_id"],
                )
                await work.evaluation_judge.update(
                    scope,
                    intent,
                    status="stopped" if unknown_failure else "settled",
                    revision=revision,
                    error=error if output is None else None,
                )
                if intent["rescore"] and not unknown_failure:
                    namespace = await work.evaluation_budget_control.namespace(
                        scope, intent["namespace_id"], lock=True
                    )
                    await work.evaluation_budget_control.close(
                        scope, intent["namespace_id"], expected_revision=namespace.revision
                    )
                await work.commit()
                count += 1
        return count


def judge_materials(case, rubric, evidence):
    dimensions = [
        d.model_dump(mode="json")
        for d in rubric.dimensions
        if not case.applicable_dimensions or d.id in case.applicable_dimensions
    ]
    # Citation presence alone does not substantiate prose. Only actual authorized
    # source bodies are evidence supplied for claim-support judging.
    supplied = {
        f"artifact:{i}": {"resource": a.resource.model_dump(mode="json"), "content": a.structure}
        for i, a in enumerate(evidence.evidence.artifacts)
    }
    supplied.update(
        {
            f"source:{i}": {"resource": a.resource.model_dump(mode="json"), "content": a.structure}
            for i, a in enumerate(evidence.sources)
        }
    )
    unavailable = {}
    reference = case.reference_answer if case.reference_confirmed else None
    for dimension in dimensions:
        if evidence.subject is MISSING:
            unavailable[dimension["id"]] = evidence.unavailable_reason or "subject_unavailable"
        elif dimension["evidence_required"] and not supplied:
            unavailable[dimension["id"]] = "evidence_unavailable"
        elif reference is None and (
            rubric.reference_policy == "required"
            or (
                rubric.reference_policy == "required_when_applicable"
                and dimension["id"] in rubric.reference_dimensions
            )
        ):
            unavailable[dimension["id"]] = "reference_unavailable"
    task = (
        case.input
        if isinstance(case.input, str)
        else [m.model_dump(mode="json") for m in case.input]
    )
    return {
        "task": task,
        "subject": None if evidence.subject is MISSING else evidence.subject,
        "reference": reference,
        "rubric": dimensions,
        "evidence": supplied,
        "unavailable": unavailable,
        "resources": [r.model_dump(mode="json") for r in evidence.resources],
        "recording": evidence.evidence.recording.model_dump(mode="json")
        if evidence.evidence.recording
        else None,
    }


async def prepare_rescore_namespace(
    work, suites, scope, principal, intent, suite, config, pair, subject
):
    from decimal import Decimal

    from app.application.evaluation.preflight import validate_current_config
    from app.domain.evaluation.budget_binding import BudgetNamespace

    current = await suites.resolved(work, scope, config.selection, principal, policy_pair=pair)
    if (
        validate_current_config(config, current)
        or not current.get("budget", {}).get("candidates")
        or suites.budgets is None
    ):
        raise ValueError("judge_configuration_changed")
    original = await work.evaluation_budget_control.namespace(scope, subject.namespace_id)
    requested = intent["rescore"]
    namespace = BudgetNamespace(
        id=intent["namespace_id"],
        suite_version_id=suite.id,
        suite_fingerprint=suite.fingerprint,
        requester=principal.model_dump(mode="json"),
        token_budget=requested["token_budget"],
        money_budget=Decimal(requested["money_budget"])
        if requested["money_budget"] is not None
        else None,
        case_ids=(subject.case_id,),
        config_versions=(subject.subject_config_version_id,),
        judge_config_version=config.id,
        repeat=subject.repeat,
        mode=original.mode,
        recording_versions=original.recording_versions,
        environment_version=original.environment_version,
        policy_revision=str(pair.execution.revision.id),
        operations_revision=str(pair.operations.revision.id),
        inventory=suites.budgets.inventory.fingerprint,
        config_fingerprints={
            str(subject.subject_config_version_id): subject.config_fingerprint,
            str(config.id): config.fingerprint,
        },
    )
    return await work.evaluation_budget_control.create(scope, namespace)
