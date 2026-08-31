"""Activity that admits and non-blockingly observes a linked child Run."""

from uuid import UUID

from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.admission import RunAdmissionService
from app.application.ports.queries import RunProjectionPort
from app.domain.execution.activity import (
    ActivityContext,
    ActivityOutcome,
    ActivityRequest,
)
from app.domain.execution.run import RunFamily, RunStatus
from app.domain.models.scope import OwnerScope


class ChildRunActivityHandler:
    activity_type = "child_run.start"
    idempotent = True

    def __init__(
        self,
        *,
        objects: ActivityObjectStore,
        admission: RunAdmissionService,
        runs: RunProjectionPort,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._objects = objects
        self._admission = admission
        self._runs = runs
        self._poll_interval = poll_interval_seconds

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
        family_value = payload.get("child_family", RunFamily.AGENT.value)
        source_type = payload.get("child_source_entity_type")
        source_id = payload.get("child_source_entity_id")
        if not all(isinstance(item, str) and item for item in (source_type, source_id)):
            return ActivityOutcome.failed(failure_code="CHILD_RUN_SOURCE_INVALID")
        # A team-owned parent run must keep its team scope for the child run,
        # otherwise the child (and its tools/evidence) silently drops to the
        # owner's personal scope and loses team visibility.
        scope = (
            OwnerScope.team(context.owner_user_id or "execution-kernel", context.team_id)
            if context.team_id
            else OwnerScope.personal(context.owner_user_id or "execution-kernel")
        )
        child_run_id = await self._admission.admit(
            family=RunFamily(str(family_value)),
            source_entity_type=str(source_type),
            source_entity_id=str(source_id),
            owner_scope=scope,
            private_input={
                key: value for key, value in payload.items() if not key.startswith("child_")
            },
            public_input={"triggered_by_run": request.aggregate_id},
            workflow={"retrieval_required": False, "tool_required": False},
            idempotency_key=f"child:{request.aggregate_id}",
            parent_run_id=UUID(request.aggregate_id),
            correlation_id=UUID(request.aggregate_id),
        )
        status = await self._runs.status_for_run(
            run_id=child_run_id,
            owner_scope=scope,
        )
        if status == RunStatus.COMPLETED:
            return ActivityOutcome.succeeded(
                result_ref=f"execution://run/{child_run_id}",
                result_summary=str(child_run_id),
            )
        if status in {RunStatus.FAILED, RunStatus.CANCELLED}:
            return ActivityOutcome.failed(failure_code=f"CHILD_RUN_{status.value.upper()}")
        return ActivityOutcome.deferred(retry_after_seconds=self._poll_interval)


__all__ = ["ChildRunActivityHandler"]
