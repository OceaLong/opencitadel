"""Accepted Run execution occupancy; locks precede inbox and event append locks."""

from uuid import UUID

from sqlalchemy import text

from app.domain.evaluation.execution_slots import ExecutionCapacityUnavailable
from app.domain.execution.run import TERMINAL_STATUSES, RunStatus
from app.domain.models.scope import OwnerScope
from app.infrastructure.repositories.db_evaluation_execution_repository import (
    DBEvaluationExecutionRepository,
)
from app.infrastructure.repositories.db_evaluation_lineage_repository import (
    DBEvaluationLineageRepository,
)

EVALUATION_SOURCES = frozenset(
    {"evaluation_recorded_case", "evaluation_isolated_case", "evaluation_judge"}
)
NEW_WORK = frozenset(
    {
        "CreateRun",
        "StartRun",
        "ResumeRun",
        "RetryRun",
        "DecideApproval",
        "RequestActivity",
        "MarkActivityCallStarted",
    }
)


class EvaluationExecutionGuard:
    def __init__(self, policy, *, session_factory, authorization):
        self.policy = policy
        self.session_factory = session_factory
        self.authorization = authorization

    async def lock_command(self, session, command):
        if command.stream_type != "run":
            return None
        try:
            run_id = UUID(command.stream_id)
        except ValueError:
            return None
        scope = (
            OwnerScope.team("execution-kernel", command.team_id)
            if command.team_id
            else OwnerScope.personal(command.owner_user_id)
        )
        repo = DBEvaluationExecutionRepository(session)
        binding = await repo.controls.binding(scope, run_id)
        if binding is None:
            source = (
                command.payload.get("source_entity_type")
                if command.command_type == "CreateRun"
                else await session.scalar(
                    text(
                        "SELECT public_payload->>'source_entity_type' FROM execution_events WHERE stream_type='run' AND stream_id=:run AND event_type='RunCreated' AND owner_user_id IS NOT DISTINCT FROM :owner AND team_id IS NOT DISTINCT FROM :team LIMIT 1"
                    ),
                    {"run": str(run_id), "owner": command.owner_user_id, "team": command.team_id},
                )
            )
            if source in EVALUATION_SOURCES:
                raise ExecutionCapacityUnavailable("execution_budget_binding_missing")
            return None
        # Team checks use the immutable original requester, not the system actor.
        scope = (
            OwnerScope.team(binding.requester["user_id"], command.team_id)
            if command.team_id
            else scope
        )
        locked = await repo.lock(scope, run_id, self.policy, require_policy=False)
        if locked[4] is None:
            raise ExecutionCapacityUnavailable("execution_prepared_lease_missing")
        return scope, locked

    async def accepted(self, session, command, state, locked):
        if locked is None:
            return
        scope, (binding, namespace, current, counters, lease) = locked
        if (
            state.source_entity_type != binding.source_entity_type
            or state.source_entity_id != binding.source_entity_id
            or state.policy_snapshot.snapshot_digest != binding.policy_digest
        ):
            raise ValueError("execution_run_binding_changed")
        if state.stream_version < lease["accepted_version"]:
            raise ValueError("execution_accepted_revision_stale")
        if state.stream_version == lease["accepted_version"]:
            if state.model_dump(mode="json") != lease["state"]:
                raise ValueError("execution_accepted_revision_conflict")
            return
        previous_state = lease["state"]
        if (
            previous_state
            and previous_state["status"] in TERMINAL_STATUSES
            and state.status not in TERMINAL_STATUSES
        ):
            raise ValueError("execution_terminal_revival_forbidden")
        repo = DBEvaluationExecutionRepository(session)
        target = (
            "released"
            if state.status == RunStatus.WAITING or state.status in TERMINAL_STATUSES
            else "held"
        )
        if target == "held" and (command.command_type in NEW_WORK or lease["phase"] != "held"):
            if current != self.policy:
                raise ExecutionCapacityUnavailable("execution_policy_changed")
            try:
                await repo.authorize(scope, binding, namespace)
                await DBEvaluationLineageRepository(session).assert_current(scope, binding.run_id)
                if binding.purpose == "evaluation_judge":
                    from app.infrastructure.repositories.db_evaluation_judge_repository import (
                        DBEvaluationJudgeRepository,
                    )

                    await DBEvaluationJudgeRepository.from_session(session).authorize_run(
                        scope, binding.run_id, state=state
                    )

            except (ValueError, PermissionError) as exc:
                raise ExecutionCapacityUnavailable("execution_admission_unavailable") from exc
        generation = lease["generation"]
        if lease["phase"] == "released" and target == "held":
            await repo.acquire(counters)
            generation += 1
        elif lease["phase"] != "released" and target == "released":
            for key, _, _ in counters:
                await session.execute(
                    text(
                        "UPDATE evaluation_execution_pools SET occupied=occupied-1 WHERE key=:key"
                    ),
                    {"key": key},
                )
            generation += 1
        await session.execute(
            text(
                "UPDATE evaluation_execution_leases SET phase=:phase,generation=:generation,accepted_version=:version,run_generation=:run_generation,state=CAST(:state AS jsonb),policy_revision=:policy WHERE run_id=:run"
            ),
            {
                "phase": target,
                "generation": generation,
                "version": state.stream_version,
                "run_generation": state.retry_generation,
                "state": state.model_dump_json(),
                "policy": current.revision,
                "run": binding.run_id,
            },
        )

    async def before_activity(self, claim, run):
        from app.application.execution.run_context import RunContextUnavailableError
        from app.domain.execution.run import RunState
        from app.infrastructure.security.db_authorization import configure_session_authorization

        try:
            async with self.session_factory() as session:
                await configure_session_authorization(session, self.authorization)
                repo = DBEvaluationExecutionRepository(session)
                binding = await repo.controls.binding(run.owner_scope, run.run_id)
                if binding is None:
                    if run.source_entity_type in EVALUATION_SOURCES:
                        raise ValueError("execution_budget_binding_missing")
                    return
                scope = (
                    OwnerScope.team(binding.requester["user_id"], run.owner_scope.team_id)
                    if run.owner_scope.team_id
                    else run.owner_scope
                )
                binding, namespace, current, _, lease = await repo.lock(
                    scope, run.run_id, self.policy, require_policy=False
                )
                # This state is read under the last blocking lease lock, after all
                # namespace/policy/pool locks; it is never a projection snapshot.
                await repo.authorize(scope, binding, namespace)
                await DBEvaluationLineageRepository(session).assert_current(scope, binding.run_id)
                if (
                    current != self.policy
                    or lease is None
                    or lease["phase"] != "held"
                    or lease["state"] is None
                ):
                    raise ValueError("execution_lease_not_active")
                state = RunState.model_validate(lease["state"])
                if (
                    state.status != RunStatus.RUNNING
                    or state.source_entity_type != run.source_entity_type
                    or state.source_entity_id != run.source_entity_id
                    or state.policy_snapshot.snapshot_digest != run.policy_snapshot.snapshot_digest
                    or claim.request.aggregate_id != str(run.run_id)
                    or claim.request.activity_id not in state.active_activity_ids
                    or not any(
                        activity_id == claim.request.activity_id
                        and generation == claim.request.generation
                        for activity_id, _, generation in state.requested_activities
                    )
                ):
                    raise ValueError("execution_activity_revision_stale")
                await session.commit()
        except (ValueError, PermissionError, ExecutionCapacityUnavailable) as exc:
            raise RunContextUnavailableError("evaluation execution admission unavailable") from exc

    async def reconcile(self, scope, run_id, *, expected_generation):
        """Recover from the verified journal; no caller state or elapsed-time grant."""
        from types import SimpleNamespace

        from app.domain.execution.aggregate import replay
        from app.domain.execution.run import RunAggregate
        from app.infrastructure.execution.postgres_event_store import PostgresEventStore
        from app.infrastructure.security.db_authorization import configure_session_authorization

        async with self.session_factory() as session:
            await configure_session_authorization(session, self.authorization)
            repo = DBEvaluationExecutionRepository(session)
            locked = await repo.lock(scope, run_id, self.policy, require_policy=False)
            lease = locked[4]
            if lease is None or lease["generation"] != expected_generation:
                raise ValueError("execution_lease_generation_stale")
            # Same final owner lock as append, strictly after all budget locks.
            key = PostgresEventStore._scope_advisory_lock_key(
                None if scope.team_id else scope.user_id, scope.team_id
            )
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            aggregate = RunAggregate()
            store = PostgresEventStore(session, event_registries={"run": aggregate.event_registry})
            events = await store.load_stream("run", str(run_id))
            if events:
                state = replay(aggregate, events, stream_id=str(run_id)).state
                await self.accepted(
                    session, SimpleNamespace(command_type="RecoverRun"), state, (scope, locked)
                )
            result = dict(
                (
                    await session.execute(
                        text("SELECT * FROM evaluation_execution_leases WHERE run_id=:run"),
                        {"run": run_id},
                    )
                )
                .mappings()
                .one()
            )
            await session.commit()
            return result
