"""Read-only Collector execution and deterministic Patrol finalization."""

from collections.abc import Callable

from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.services.patrol_collector_validator import (
    MCPPatrolCollectorValidator,
)
from app.application.services.patrol_run_service import PatrolRunService
from app.domain.execution.activity import (
    ActivityContext,
    ActivityOutcome,
    ActivityRequest,
)
from app.domain.models.patrol import PatrolPackConfig
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork


class PatrolExecutionActivityHandler:
    activity_type = "patrol.execute"
    idempotent = True

    def __init__(
        self,
        *,
        objects: ActivityObjectStore,
        uow_factory: Callable[[], IUnitOfWork],
        collector: MCPPatrolCollectorValidator,
        runs: PatrolRunService,
    ) -> None:
        self._objects = objects
        self._uow_factory = uow_factory
        self._collector = collector
        self._runs = runs

    async def execute(
        self,
        request: ActivityRequest,
        context: ActivityContext,
    ) -> ActivityOutcome:
        if request.input_ref is None:
            return ActivityOutcome.failed(failure_code="ACTIVITY_INPUT_MISSING")
        payload = await self._objects.load_input(
            key=request.input_ref,
            expected_digest=request.input_digest,
        )
        patrol_run_id = payload.get("patrol_run_id")
        session_id = payload.get("session_id")
        if not isinstance(patrol_run_id, str) or not isinstance(session_id, str):
            return ActivityOutcome.failed(failure_code="PATROL_INPUT_INVALID")
        scope = (
            OwnerScope.personal(context.owner_user_id)
            if context.owner_user_id
            else OwnerScope.team("execution-kernel", context.team_id or "")
        )
        run = await self._runs.get_run(patrol_run_id, scope)
        config = PatrolPackConfig.model_validate(run.pack_snapshot["config"])
        async with self._uow_factory() as uow:
            server = await uow.mcp_server.get_by_id(
                str(run.pack_snapshot["mcp_server_id"]),
                scope=scope,
            )
        if server is None:
            return ActivityOutcome.failed(failure_code="PATROL_COLLECTOR_MISSING")
        submissions = await self._collector.collect(
            server,
            config,
            policy=context.run.policy_snapshot.common.activity,
        )
        finalized = await self._runs.finalize_run(
            run_id=run.id,
            session_id=session_id,
            idempotency_key=run.submission_idempotency_key,
            collector_capability_hash=run.collector_capability_hash,
            submissions=submissions,
        )
        result_ref = await self._objects.put_result(
            request.activity_id,
            {
                "patrol_run_id": finalized.id,
                "status": finalized.status.value,
                "pass_count": finalized.pass_count,
                "warn_count": finalized.warn_count,
                "fail_count": finalized.fail_count,
                "error_count": finalized.error_count,
            },
        )
        return ActivityOutcome.succeeded(
            result_ref=result_ref,
            result_summary=finalized.status.value,
        )


__all__ = ["PatrolExecutionActivityHandler"]
