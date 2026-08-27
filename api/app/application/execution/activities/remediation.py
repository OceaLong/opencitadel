"""Durable governed remediation Activity."""

from typing import Protocol

from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.services.runtime_policy_reader import OperationsPolicyReader
from app.domain.execution.activity import (
    ActivityContext,
    ActivityOutcome,
    ActivityRequest,
)
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import ActivityExecutionPolicy, PatrolRemediationMode
from app.domain.utils.time_utils import utc_now


class RemediationExecutor(Protocol):
    async def execute(
        self,
        remediation_id: str,
        session_id: str,
        idempotency_key: str,
        scope: OwnerScope,
        *,
        policy: ActivityExecutionPolicy,
    ) -> dict: ...


class RemediationActivityHandler:
    activity_type = "remediation.execute"
    idempotent = True

    def __init__(
        self,
        *,
        objects: ActivityObjectStore,
        executor: RemediationExecutor,
        policy_reader: OperationsPolicyReader,
    ) -> None:
        self._objects = objects
        self._executor = executor
        self._policy_reader = policy_reader

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
        remediation_id = payload.get("remediation_id")
        session_id = payload.get("session_id")
        if not isinstance(remediation_id, str) or not isinstance(session_id, str):
            return ActivityOutcome.failed(failure_code="REMEDIATION_INPUT_INVALID")
        scope = (
            OwnerScope.personal(context.owner_user_id)
            if context.owner_user_id
            else OwnerScope.team("execution-kernel", context.team_id or "")
        )
        active = await self._policy_reader.active_operations(
            require_fresh=True,
            now=utc_now(),
        )
        if active.revision.policy.patrol.remediation is not PatrolRemediationMode.ENABLED:
            return ActivityOutcome.failed(failure_code="POLICY_DENIED")
        result = await self._executor.execute(
            remediation_id,
            session_id,
            context.idempotency_key,
            scope,
            policy=context.run.policy_snapshot.common.activity,
        )
        result_ref = await self._objects.put_result(request.activity_id, result)
        return ActivityOutcome.succeeded(
            result_ref=result_ref,
            result_summary=str(result.get("status") or "executed"),
        )


__all__ = ["RemediationActivityHandler"]
