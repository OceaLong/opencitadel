"""Durable kernel scheduler. Each tick claims one batch fairly and admits bounded work.

The batch lock always precedes C1 namespace locks. No execution consumer acquires
batch locks. Input preparation does not hold batch locks or grant execution permits.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from app.application.evaluation.budget_admission import (
    prepare_budget_binding,
    prepare_budget_namespace,
)
from app.application.evaluation.configuration_bridge import f07_configuration_evidence
from app.application.evaluation.replay_admission import prepare_replay_binding
from app.domain.evaluation.batch import (
    TERMINAL_BATCH,
    TERMINAL_EXECUTION,
    aggregate_status,
    schedule_slots,
)
from app.domain.evaluation.budget_binding import BudgetBindingSelection
from app.domain.evaluation.errors import DatasetNotFound
from app.domain.evaluation.execution_slots import ExecutionCapacityUnavailable
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.family import RunFamily
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal
from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot

KERNEL = AuthorizationContext.system("execution-kernel")


@dataclass(frozen=True)
class TickResult:
    claimed: int = 0
    dispatched: int = 0
    settled: int = 0


class Scheduler:
    def __init__(
        self,
        uow_factory,
        suites,
        admission,
        *,
        execution_policy,
        preflight_factory,
        environments=None,
        dispatch_limit=10,
    ):
        if not 1 <= dispatch_limit <= 100:
            raise ValueError("invalid_dispatch_limit")
        self.uow_factory, self.suites, self.admission = uow_factory, suites, admission
        self.execution_policy, self.preflight_factory = execution_policy, preflight_factory
        self.environments, self.dispatch_limit = environments, dispatch_limit

    async def tick(self, now):
        async with self.uow_factory(KERNEL) as work:
            claim = await work.evaluation_batch.claim(now)
            await work.commit()
        if claim is None:
            return TickResult()
        scope, principal = (
            OwnerScope.model_validate(claim["scope_body"]),
            Principal.model_validate(claim["principal"]),
        )
        dispatched = settled = 0
        async with self.renewing(claim):
            try:
                if claim["status"] in {"created", "validating"}:
                    await self._materialize(claim, scope, principal)
                settled = await self._reconcile(claim, scope, now)
                async with self.uow_factory(KERNEL) as work:
                    repo = work.evaluation_batch
                    current = await repo.get(scope, claim["id"])
                    rows = (
                        await repo.dispatch_candidates(claim, now, limit=self.dispatch_limit)
                        if current["status"] in {"queued", "running", "waiting"}
                        else []
                    )
                    await work.commit()
                if current["status"] in {"queued", "running", "waiting"}:
                    for row in rows:
                        if dispatched >= self.dispatch_limit:
                            break
                        if row["execution_status"] not in {"queued", "waiting"} or row["envelope"]:
                            continue
                        try:
                            if await self._dispatch(claim, scope, principal, row):
                                dispatched += 1
                        except (ValueError, PermissionError, ExecutionCapacityUnavailable) as error:
                            code = str(error)
                            if code in {"claim_lost", "batch_dispatch_stopped"}:
                                break
                            waiting = "capacity" in code or "lease_not_ready" in code
                            case_expired = code == "case_dispatch_stopped"
                            async with self.uow_factory(KERNEL) as work:
                                repo = work.evaluation_batch
                                await repo.fence(claim)
                                await repo.update_result(
                                    scope,
                                    claim["id"],
                                    row,
                                    "waiting"
                                    if waiting
                                    else "failed"
                                    if case_expired
                                    else "blocked",
                                    scoring=None if waiting else "skipped",
                                    error="admission_capacity"
                                    if waiting
                                    else "case_timeout"
                                    if case_expired
                                    else "admission_unavailable",
                                )
                                await work.commit()
                await self._reconcile(claim, scope, now)
                return TickResult(claimed=1, dispatched=dispatched, settled=settled)
            finally:
                async with self.uow_factory(KERNEL) as work:
                    await work.evaluation_batch.release(claim)
                    await work.commit()

    @asynccontextmanager
    async def renewing(self, claim, *, interval_seconds=10):
        """Structured heartbeat: lease loss cancels work; exit joins the heartbeat."""

        async def heartbeat():
            while True:
                await asyncio.sleep(interval_seconds)
                async with self.uow_factory(KERNEL) as work:
                    await work.evaluation_batch.renew(claim)
                    await work.commit()

        try:
            async with asyncio.TaskGroup() as tasks:
                renewal = tasks.create_task(heartbeat(), name="evaluation-claim-renewal")
                try:
                    yield
                finally:
                    renewal.cancel()
        except BaseExceptionGroup as errors:
            if len(errors.exceptions) == 1:
                raise errors.exceptions[0] from None
            raise

    async def _materialize(self, claim, scope, principal):
        async with self.uow_factory(KERNEL) as work:
            repo = work.evaluation_batch
            await repo.fence(claim)
            await repo.set_status(scope, claim["id"], "validating")
            await work.commit()
        pair = await self.suites.policies.load_active_pair()
        try:
            suite = await self.suites.get_version(scope, principal, "suite", claim["suite_version"])
            rubric = await self.suites.get_version(scope, principal, "rubric", suite.rubric_version)
            dataset = await self.suites.datasets.get_version(
                scope, principal, suite.dataset_version
            )
            slots = schedule_slots(
                [case.id for case in dataset.cases],
                suite.config_versions,
                suite.settings.repeat,
                suite.settings.seed,
            )
            async with self.uow_factory(KERNEL) as work:
                repo = work.evaluation_batch
                await repo.fence(claim)
                check = await self.preflight_factory(principal).revalidate_for_start(
                    scope, suite.id, uow=work, policy_pair=pair
                )
                if not check.allowed:
                    raise ValueError("batch_preflight_rejected")
                if claim["selected_slots"]:
                    parent = await repo.results(scope, claim["parent_batch"], limit=5000)
                    for row in parent:
                        if str(row["id"]) in claim["selected_slots"] and (
                            row["recovery_pending"]
                            or await repo.unknown_effect(
                                scope, row["run_id"], include_unresolved=True
                            )
                        ):
                            raise ValueError("retry_effect_unresolved")
                    selected = {
                        (row["case_revision_id"], row["config_version_id"], row["repetition"])
                        for row in parent
                        if str(row["id"]) in claim["selected_slots"]
                    }
                    slots = tuple(
                        slot
                        for slot in slots
                        if (slot.case_revision_id, slot.config_version_id, slot.repetition)
                        in selected
                    )
                from app.domain.evaluation.review import case_review_requirements

                await repo.materialize(
                    claim,
                    slots,
                    suite.settings.model_dump(mode="json"),
                    review_required=any(case_review_requirements(dataset, rubric).values()),
                )
                await work.evaluation_review.requirements(
                    scope, claim["id"], case_review_requirements(dataset, rubric)
                )
                await work.commit()
        except (ValueError, PermissionError):
            async with self.uow_factory(KERNEL) as work:
                repo = work.evaluation_batch
                await repo.fence(claim)
                await repo.set_status(scope, claim["id"], "rejected", error="validation_failed")
                await work.commit()

    async def _dispatch(self, claim, scope, principal, row):
        # Lookup first under current original requester authority; UUID is never acceptance.
        async with self.uow_factory(KERNEL) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=True)
            repo = work.evaluation_batch
            if await repo.receipt(scope, row):
                return False
        pair = await self.suites.policies.load_active_pair()
        suite = await self.suites.get_version(scope, principal, "suite", claim["suite_version"])
        config = await self.suites.get_version(scope, principal, "config", row["config_version_id"])
        dataset = await self.suites.datasets.get_version(scope, principal, suite.dataset_version)
        case = next(case for case in dataset.cases if case.id == row["case_revision_id"])
        async with self.uow_factory(KERNEL) as work:
            repo = work.evaluation_batch
            await repo.fence(claim, dispatch=True, result_id=row["id"])
            await prepare_budget_namespace(
                work,
                self.suites,
                scope,
                principal,
                namespace_id=claim["id"],
                suite_version_id=suite.id,
                policy_pair=pair,
            )
            availability = await repo.budget_availability(claim["id"], config)
            if availability != "ready":
                await repo.update_result(
                    scope,
                    claim["id"],
                    row,
                    availability,
                    scoring="skipped" if availability == "blocked_budget" else None,
                )
                await work.commit()
                return False
            await work.commit()
        environment_id = uuid5(row["run_id"], "environment")
        if suite.mode == "isolated":
            if self.environments is None:
                raise ValueError("environment_service_unavailable")
            from app.domain.evaluation.environment import CaseSlot as EnvironmentSlot

            async with self.uow_factory(KERNEL) as work:
                repo = work.evaluation_batch
                await repo.fence(claim, dispatch=True, result_id=row["id"])
                await prepare_budget_namespace(
                    work,
                    self.suites,
                    scope,
                    principal,
                    namespace_id=claim["id"],
                    suite_version_id=suite.id,
                    policy_pair=pair,
                )
                lease = await self.environments.allocate_in_uow(
                    work,
                    scope,
                    principal,
                    suite.environment_version,
                    EnvironmentSlot(
                        workspace="team:" + scope.team_id
                        if scope.team_id
                        else "user:" + scope.user_id,
                        batch_id=claim["id"],
                        case_id=row["case_revision_id"],
                        config_version=config.id,
                        repeat=row["repetition"] + 1,
                    ),
                    lease_id=environment_id,
                    generation=row["attempt"] + 1,
                )
                await repo.set_cleanup(scope, claim["id"], "pending")
                if lease.state != "ready":
                    await repo.update_result(scope, claim["id"], row, "waiting")
                    await work.commit()
                    return False
                await work.commit()
        attachments = []
        async with self.uow_factory(KERNEL) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=True)
            for identity in case.attachments:
                file = await work.file.get_by_id(identity, scope=scope)
                if file is None:
                    raise ValueError("case_attachment_unavailable")
                attachments.append(file)
        private = private_case_input(case, config, attachments)
        snapshot = derive_run_policy_snapshot(pair.execution, RunFamily(config.selection.mode))
        intent = {
            "private_input": private,
            "policy": snapshot.model_dump(mode="json"),
            "config": str(config.id),
            "suite": str(suite.id),
            "operations": str(pair.operations.revision.id),
        }
        async with self.uow_factory(KERNEL) as work:
            repo = work.evaluation_batch
            await repo.fence(claim, dispatch=True, result_id=row["id"])
            await repo.save_intent(scope, row, intent)
            await work.commit()
        scheduler = self

        async def configuration_resolver(owner, run_id, actual_policy):
            if actual_policy != pair.execution.revision.policy:
                raise ValueError("admission_policy_changed")
            evidence = f07_configuration_evidence(
                scope, config, pair.execution, purpose="evaluation_subject"
            )
            evidence["stage"] = "admission"
            original_authorization = AuthorizationContext.for_principal(principal, scope=scope)
            async with scheduler.uow_factory(original_authorization) as work:
                await work.evaluation_dataset.authorize(scope, principal, write=True)
                evidence["physical_requester"] = await work.execution_usage.capture_requester(
                    scope, original_authorization, run_id=run_id
                )
                identity = await work.execution_usage.admission_snapshot(
                    scope, run_id, evidence, "evaluation_subject"
                )
                await work.commit()
                return identity

        class Sink:
            async def receive(self, envelope, *, max_active_runs=0):
                if envelope.payload["policy_snapshot"] != intent["policy"]:
                    raise ValueError("admission_policy_changed")
                # Known input/configuration references survive a later C1 rollback.
                # This checkpoint grants no execution permit or accepted receipt.
                async with scheduler.uow_factory(KERNEL) as work:
                    await work.evaluation_batch.fence(claim)
                    await work.evaluation_batch.prepared(scope, row, envelope)
                    await work.commit()
                async with scheduler.uow_factory(KERNEL) as work:
                    repo = work.evaluation_batch
                    await repo.fence(claim, dispatch=True, result_id=row["id"])
                    await work.evaluation_dataset.authorize(scope, principal, write=True)
                    await prepare_budget_namespace(
                        work,
                        scheduler.suites,
                        scope,
                        principal,
                        namespace_id=claim["id"],
                        suite_version_id=suite.id,
                        policy_pair=pair,
                    )
                    if await repo.unknown_effect(scope, row["run_id"], include_unresolved=True):
                        raise ValueError("retry_effect_unresolved")
                    if suite.mode == "recorded":
                        await prepare_replay_binding(
                            work,
                            scheduler.suites,
                            scope,
                            principal,
                            run_id=row["run_id"],
                            source_entity_id=str(row["id"]) + ":" + str(row["attempt"]),
                            config_version_id=config.id,
                            recording_version_id=recording_for_configuration(suite, config),
                            policy_pair=pair,
                            policy_snapshot=snapshot,
                        )
                    else:
                        from app.application.evaluation.environment_admission import (
                            prepare_environment_binding,
                        )

                        await prepare_environment_binding(
                            work,
                            scheduler.suites,
                            scope,
                            principal,
                            run_id=row["run_id"],
                            source_entity_id=str(row["id"]) + ":" + str(row["attempt"]),
                            config_version_id=config.id,
                            lease_id=environment_id,
                            policy_pair=pair,
                            policy_snapshot=snapshot,
                            registry=scheduler.environments.registry,
                            ceiling=scheduler.environments.ceiling,
                        )
                    await prepare_budget_binding(
                        work,
                        scheduler.suites,
                        scope,
                        principal,
                        selection=BudgetBindingSelection(
                            namespace_id=claim["id"],
                            run_id=row["run_id"],
                            source_entity_id=str(row["id"]) + ":" + str(row["attempt"]),
                            case_id=row["case_revision_id"],
                            config_version_id=config.id,
                            subject_config_version_id=config.id,
                            repeat=row["repetition"] + 1,
                        ),
                        policy_pair=pair,
                        policy_snapshot=snapshot,
                    )
                    if row["predecessor_run_id"] is not None:
                        await work.evaluation_lineage.link_replacement(
                            scope,
                            row["run_id"],
                            predecessor_run_id=row["predecessor_run_id"],
                            expected_generation=row["predecessor_generation"],
                        )
                    await work.evaluation_execution.prepare(
                        scope, row["run_id"], scheduler.execution_policy
                    )
                    received = await work.execution_commands.receive(
                        envelope, max_active_runs=max_active_runs
                    )
                    await repo.submitted(scope, claim["id"], row, envelope)
                    await work.commit()
                    return received

        if row["prepared_envelope"] is not None:
            await Sink().receive(CommandEnvelope.model_validate(row["prepared_envelope"]))
            return True
        await self.admission.admit(
            family=RunFamily(config.selection.mode),
            source_entity_type="evaluation_recorded_case"
            if suite.mode == "recorded"
            else "evaluation_isolated_case",
            source_entity_id=str(row["id"]) + ":" + str(row["attempt"]),
            owner_scope=scope,
            private_input=private,
            public_input={},
            workflow={
                "evaluation_suite_version": str(suite.id),
                "evaluation_config_version": str(config.id),
            },
            idempotency_key=row["admission_key"],
            run_id=row["run_id"],
            command_sink=Sink(),
            usage_purpose="evaluation_subject",
            configuration_resolver=configuration_resolver,
        )
        return True

    async def _reconcile(self, claim, scope, now):
        settled = 0
        async with self.uow_factory(KERNEL) as work:
            repo = work.evaluation_batch
            batch = await repo.fence(claim)
            # Keep namespace before execution and environment locks for the whole pass.
            try:
                namespace = await work.evaluation_budget_control.namespace(
                    scope, claim["id"], lock=True
                )
            except DatasetNotFound:
                namespace = None
            cancel = await repo.cancellation_requested(scope, claim["id"])
            timeout = batch["deadline"] is not None and batch["deadline"] <= now
            cancelling = cancel or timeout or batch["status"] == "cancelling"
            if cancelling and batch["status"] not in {
                "cancelled",
                "failed",
                "completed",
                "completed_with_errors",
                "rejected",
            }:
                await repo.set_status(
                    scope, claim["id"], "cancelling", error="batch_timeout" if timeout else None
                )
                batch["status"] = "cancelling"
            rows = await repo.results(scope, claim["id"], limit=5000)
            for row in rows:
                await repo.observe_unknown(scope, claim["id"], row)
                case_timeout = (
                    row["started_at"] is not None
                    and row["started_at"]
                    + timedelta(seconds=batch["settings"]["case_timeout_seconds"])
                    <= now
                    and row["execution_status"] not in TERMINAL_EXECUTION
                )
                stop_case = cancelling or case_timeout
                receipt = await repo.receipt(scope, row)
                if receipt is None:
                    if (
                        stop_case
                        and not row["envelope"]
                        and row["execution_status"] not in TERMINAL_EXECUTION
                    ):
                        await repo.update_result(
                            scope,
                            claim["id"],
                            row,
                            "failed" if case_timeout and not cancelling else "cancelled",
                            scoring="skipped",
                            error="case_timeout" if case_timeout else None,
                        )
                        settled += 1
                    continue
                if not row["envelope"] and row["execution_status"] not in TERMINAL_EXECUTION:
                    await repo.attach_received(scope, claim["id"], row)
                if stop_case and receipt["status"] != "accepted":
                    await work.evaluation_execution.withdraw_unaccepted(
                        scope, row["run_id"], row["command_id"], self.execution_policy
                    )
                    receipt = await repo.receipt(scope, row)
                if receipt["status"] in {"rejected", "dead_lettered"}:
                    await work.evaluation_execution.withdraw_unaccepted(
                        scope, row["run_id"], row["command_id"], self.execution_policy
                    )
                    if row["execution_status"] not in TERMINAL_EXECUTION:
                        await repo.update_result(
                            scope,
                            claim["id"],
                            row,
                            "cancelled" if cancelling else "failed",
                            scoring="skipped",
                            error="case_timeout"
                            if case_timeout and not cancelling
                            else "admission_rejected",
                        )
                        await repo.record_receipt(scope, row, receipt)
                        settled += 1
                    continue
                if receipt["status"] != "accepted":
                    continue
                projection = await repo.projection(scope, row)
                if projection and projection["stream_version"] > row["run_revision"]:
                    status = {
                        "completed": "succeeded",
                        "failed": "failed",
                        "cancelled": "cancelled",
                        "waiting": "waiting",
                    }.get(projection["status"], "running")
                    state = projection["state"]
                    if case_timeout and not cancelling and status == "cancelled":
                        status = "failed"
                    unknown = (
                        state.get("failure_code") == "NON_IDEMPOTENT_OUTCOME_UNKNOWN"
                        or any(item[1] == "unknown" for item in state.get("settled_activities", []))
                        or any(
                            item[2] == "NON_IDEMPOTENT_OUTCOME_UNKNOWN"
                            for item in state.get("activity_failure_codes", [])
                        )
                    )
                    unknown = unknown or await repo.unknown_effect(scope, row["run_id"])
                    if unknown and projection["terminal"]:
                        status = "unknown"
                    if row["execution_status"] in TERMINAL_EXECUTION:
                        await repo.event(
                            scope,
                            claim["id"],
                            "late_run_evidence",
                            result_id=row["id"],
                            evidence={"run_revision": projection["stream_version"]},
                        )
                    else:
                        await repo.update_result(
                            scope,
                            claim["id"],
                            row,
                            status,
                            scoring="skipped"
                            if status in {"failed", "cancelled", "unknown"}
                            else None,
                            unknown=unknown,
                            error="case_timeout"
                            if case_timeout and not cancelling
                            else state.get("failure_code"),
                        )
                    await repo.record_receipt(scope, row, receipt, projection["stream_version"])
                    if projection["terminal"]:
                        settled += 1
                if (
                    stop_case
                    and not row["cancel_sent"]
                    and not (projection and projection["terminal"])
                ):
                    command = CommandEnvelope(
                        command_id=uuid5(NAMESPACE_URL, f"evaluation-cancel:{row['run_id']}"),
                        command_type="CancelRun",
                        command_schema_version=1,
                        stream_type="run",
                        stream_id=str(row["run_id"]),
                        owner_user_id=None if scope.team_id else scope.user_id,
                        team_id=scope.team_id,
                        correlation_id=row["run_id"],
                        causation_id=None,
                        issued_at=now,
                        payload={
                            "reason": "batch_timeout"
                            if timeout
                            else "case_timeout"
                            if case_timeout
                            else "requested_by_user"
                        },
                    )
                    await work.execution_commands.receive(command)
                    await repo.cancel_submitted(scope, row, command.command_id)
            rows = await repo.results(scope, claim["id"], limit=5000)
            if self.environments is not None:
                leases = await repo.environment_leases(scope, claim["id"])
                states = []
                for lease in leases:
                    matching = [
                        row
                        for row in rows
                        if str(row["case_revision_id"]) == lease["case_slot"]["case_id"]
                        and str(row["config_version_id"]) == lease["case_slot"]["config_version"]
                        and row["repetition"] + 1 == lease["case_slot"]["repeat"]
                    ]
                    if matching and all(
                        row["execution_status"] in TERMINAL_EXECUTION for row in matching
                    ):
                        cleaned = await self.environments.cleanup_in_uow(work, scope, lease["id"])
                        states.append(cleaned.state)
                    else:
                        states.append(lease["state"])
                cleanup = (
                    "failed"
                    if "quarantine" in states
                    else "pending"
                    if any(state != "verified_clean" for state in states)
                    else "clean"
                )
                if cleanup != batch["cleanup_status"]:
                    await repo.set_cleanup(scope, claim["id"], cleanup)
                    await repo.event(scope, claim["id"], "cleanup_changed")
            if batch["status"] not in TERMINAL_BATCH:
                principal = Principal.model_validate(batch["principal"])
                for row in rows:
                    expired = row["started_at"] is not None and row["started_at"] + timedelta(
                        seconds=batch["settings"]["case_timeout_seconds"]
                    ) <= max(now, batch["database_now"])
                    if cancelling or expired:
                        if row["recovery_pending"]:
                            await repo.set_recovery(
                                scope,
                                claim["id"],
                                row,
                                False,
                                reason="batch_stopped" if cancelling else "case_timeout",
                            )
                        continue
                    predecessor = await repo.retry_predecessor(scope, row)
                    if predecessor is None:
                        if row["recovery_pending"]:
                            await repo.set_recovery(
                                scope, claim["id"], row, False, reason="retry_ineligible"
                            )
                        continue
                    try:
                        await work.evaluation_dataset.authorize(scope, principal, write=True)
                    except PermissionError:
                        await repo.set_recovery(
                            scope, claim["id"], row, False, reason="retry_authorization_revoked"
                        )
                        continue
                    if predecessor.cleanup == "pending":
                        await repo.set_recovery(scope, claim["id"], row, True)
                    elif predecessor.cleanup == "failed":
                        await repo.set_recovery(
                            scope, claim["id"], row, False, reason="cleanup_failed"
                        )
                    else:
                        await repo.replace_attempt(
                            scope, claim["id"], row, predecessor.generation, now
                        )
                rows = await repo.results(scope, claim["id"], limit=5000)
            judge_active = []
            if hasattr(work, "evaluation_judge"):
                if cancelling:
                    await work.evaluation_judge.cancel(
                        scope, claim["id"], self.execution_policy, now
                    )
                judge_active = await work.evaluation_judge.active(scope, claim["id"])
            status = aggregate_status(
                batch["status"],
                ["waiting" if row["recovery_pending"] else row["execution_status"] for row in rows],
                [row["scoring_status"] for row in rows],
            )
            if judge_active and status in TERMINAL_BATCH and batch["status"] not in TERMINAL_BATCH:
                status = "cancelling" if cancelling else "running"
            if status == "cancelled" and (timeout or batch["error"] == "batch_timeout"):
                status = "failed"
            if status != batch["status"]:
                await repo.set_status(scope, claim["id"], status)
            if status in TERMINAL_BATCH and namespace is not None and namespace.state == "open":
                await work.evaluation_budget_control.close(
                    scope, claim["id"], expected_revision=namespace.revision
                )
            await work.commit()
        return settled


def private_case_input(case, config, attachments):
    """Map fixed case/configuration to the existing AGENT/ASK private input contract."""
    from app.application.services.agent_service import _private_attachment

    conversation = list(case.history)
    if isinstance(case.input, str):
        message = case.input
    else:
        conversation += list(case.input[:-1])
        if case.input[-1].role != "user":
            raise ValueError("case_final_user_message_required")
        message = case.input[-1].content
    if len(conversation) > 100:
        raise ValueError("case_conversation_limit")
    if config.selection.prompt:
        message = config.selection.prompt + "\n\n" + message
    resources = {
        (r.resource_kind, r.resource_id, r.resource_version): r
        for r in (*case.resources, *config.selection.resources)
        if r.resource_kind == "knowledge_base"
    }
    if len(resources) > 8:
        raise ValueError("case_resource_limit")
    return {
        "message": message,
        "conversation": [item.model_dump(mode="json") for item in conversation],
        "mode": config.selection.mode,
        "model_id": config.selection.model_id,
        "skill_id": config.selection.skill_id,
        "temperature_override": config.selection.temperature,
        "attachments": [_private_attachment(file) for file in attachments],
        "resource_bindings": [
            {
                "binding_id": str(
                    uuid5(NAMESPACE_URL, f"evaluation-resource:{kind}:{identity}:{version}")
                ),
                "resource_kind": kind,
                "resource_id": identity,
                "version_id": version,
                "is_current": False,
            }
            for kind, identity, version in resources
        ],
    }


def recording_for_configuration(suite, config):
    reference = config.selection.external_contract_ref
    if reference is not None:
        if reference.kind != "recording" or reference.version_id not in suite.recording_versions:
            raise ValueError("recording_reference_mismatch")
        return reference.version_id
    if not suite.recording_versions:
        raise ValueError("recording_reference_missing")
    return min(suite.recording_versions, key=str)
