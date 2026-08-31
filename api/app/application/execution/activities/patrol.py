"""Read-only Collector execution and deterministic Patrol finalization."""

from collections.abc import Callable

from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.services.patrol_collector_validator import (
    MCPPatrolCollectorValidator,
)
from app.application.services.patrol_pack_service import PatrolPackService
from app.application.services.patrol_run_service import PatrolRunService
from app.domain.errors import ConflictError
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


class PatrolValidationActivityHandler:
    """Run live Collector validation only inside the execution kernel."""

    activity_type = "patrol.validate"
    idempotent = True

    def __init__(
        self,
        *,
        objects: ActivityObjectStore,
        uow_factory: Callable[[], IUnitOfWork],
        collector: MCPPatrolCollectorValidator,
        packs: PatrolPackService,
    ) -> None:
        self._objects = objects
        self._uow_factory = uow_factory
        self._collector = collector
        self._packs = packs

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
        pack_id = payload.get("pack_id")
        pack_version = payload.get("pack_version")
        validation_run_id = payload.get("validation_run_id")
        actor_user_id = payload.get("actor_user_id")
        if (
            not isinstance(pack_id, str)
            or not isinstance(pack_version, int)
            or not isinstance(validation_run_id, str)
            or not isinstance(actor_user_id, str)
        ):
            return ActivityOutcome.failed(failure_code="PATROL_VALIDATION_INPUT_INVALID")
        if str(context.run.run_id) != validation_run_id:
            return ActivityOutcome.failed(failure_code="PATROL_VALIDATION_RUN_MISMATCH")

        scope = (
            OwnerScope.personal(context.owner_user_id)
            if context.owner_user_id
            else OwnerScope.team("execution-kernel", context.team_id or "")
        )
        try:
            pack = await self._packs.get_pack(pack_id, scope)
        except (OSError, RuntimeError, ValueError):
            return ActivityOutcome.failed(failure_code="PATROL_PACK_MISSING")
        if pack.version != pack_version or pack.validation_run_id != validation_run_id:
            return ActivityOutcome.failed(failure_code="PATROL_VALIDATION_STALE")

        async with self._uow_factory() as uow:
            server = await uow.mcp_server.get_by_id(pack.mcp_server_id, scope=scope)
        capabilities: dict = {}
        dry_run: dict = {}
        errors: list[str] = []
        if server is None:
            errors.append("Collector unavailable: integration is missing")
        else:
            try:
                capabilities = await self._collector.get_capabilities(
                    server,
                    policy=context.run.policy_snapshot.common.activity,
                )
                dry_run = await self._collector.dry_run(
                    server,
                    pack.config,
                    policy=context.run.policy_snapshot.common.activity,
                )
            except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
                errors.append(f"Collector unavailable: {str(exc)[:500]}")

        try:
            validated = await self._packs.complete_validation(
                pack_id=pack_id,
                scope=scope,
                actor_user_id=actor_user_id,
                validation_run_id=validation_run_id,
                validated_version=pack_version,
                capabilities=capabilities,
                dry_run=dry_run,
                errors=errors,
            )
        except ConflictError:
            return ActivityOutcome.failed(failure_code="PATROL_VALIDATION_STALE")
        result_ref = await self._objects.put_result(
            request.activity_id,
            {
                "pack_id": validated.id,
                "pack_version": validated.version,
                "status": validated.status.value,
                "ok": bool(validated.validation_summary.get("ok")),
            },
        )
        return ActivityOutcome.succeeded(
            result_ref=result_ref,
            result_summary=validated.status.value,
        )


__all__ = ["PatrolExecutionActivityHandler", "PatrolValidationActivityHandler"]
