"""Durable governed remediation Activity."""

from hashlib import sha256
from typing import Protocol
from uuid import UUID

from app.application.execution import activity_types
from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.services.runtime_policy_reader import OperationsPolicyReader
from app.domain.execution.activity import (
    ActivityContext,
    ActivityOutcome,
    ActivityRequest,
)
from app.domain.execution.effect import EffectOperationSpec
from app.domain.execution.serialization import canonical_json_bytes
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
    activity_type = activity_types.REMEDIATION_EXECUTE
    # A remediation performs external side effects (actuator writes). After a
    # crash the outcome of an already-started call is unknowable, so recovery
    # must NOT blindly replay it: the worker settles the activity as
    # outcome-unknown instead (K2-8 幂等复核).
    idempotent = False

    def __init__(
        self,
        *,
        objects: ActivityObjectStore,
        executor: RemediationExecutor,
        policy_reader: OperationsPolicyReader,
        isolated=None,
        effects=None,
    ) -> None:
        self._effects = effects
        self._isolated = isolated
        self._objects = objects
        self._executor = executor
        self._policy_reader = policy_reader

    async def execute(
        self,
        request: ActivityRequest,
        context: ActivityContext,
    ) -> ActivityOutcome:
        if self._isolated is not None and await self._isolated.active(context):
            return await self._isolated.remediation(context)
        if getattr(context.run, "source_entity_type", None) == "evaluation_isolated_case":
            raise ValueError("environment_binding_missing")
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
        if self._effects is not None:
            remediation = await self._executor.get(remediation_id, scope)
            operation_id = UUID(remediation.actuator_operation_id)
            spec = EffectOperationSpec(
                operation_id=operation_id,
                invocation_id=operation_id,
                run_id=context.run.run_id,
                activity_id=request.activity_id,
                generation=request.generation,
                provider_identity="ops-actuator",
                provider_locator={
                    "remediation_id": remediation_id,
                    "action": remediation.action.value,
                },
                contract_digest=remediation.actuator_capability_hash,
                arguments_digest=remediation.actuator_payload_digest,
                target_digest=sha256(
                    canonical_json_bytes(
                        {
                            "cluster": remediation.actuator_target_ref,
                            "uid": remediation.actuator_target_uid,
                            "namespace": remediation.target_namespace,
                            "workload": remediation.target_workload,
                            "kind": remediation.target_kind.lower(),
                        }
                    )
                ).hexdigest(),
                idempotency_key=remediation.idempotency_key,
                capability="dedupe_resolve",
                output_schema={
                    "type": "object",
                    "required": ["after_observation"],
                    "properties": {"after_observation": {"type": "object"}},
                },
            )

            async def send():
                return await self._executor.execute(
                    remediation_id,
                    session_id,
                    context.idempotency_key,
                    scope,
                    policy=context.run.policy_snapshot.common.activity,
                    persist_outcome=False,
                )

            def interpret(value):
                state = (value.get("actuator_receipt") or {}).get("state", "unknown")
                complete = (
                    value
                    if state == "applied" and isinstance(value.get("after_observation"), dict)
                    else None
                )
                return state, complete

            outcome = await self._effects.invoke(spec, context, send, interpret=interpret)
            # Formal effect commit precedes the Patrol/read-side recheck projection.
            result = outcome.result or {
                "status": "unknown",
                "actuator_operation_id": str(operation_id),
            }
            if outcome.state == "not_applied":
                result["status"] = "failed"
            await self._executor.apply_effect_fact(
                remediation_id,
                scope,
                state=outcome.state,
                result=outcome.result,
                receipt=result.get("actuator_receipt"),
            )
        else:
            result = await self._executor.execute(
                remediation_id,
                session_id,
                context.idempotency_key,
                scope,
                policy=context.run.policy_snapshot.common.activity,
            )
        result_ref = await self._objects.put_result(
            request.activity_id, result, run_id=context.run.run_id
        )
        status = result.get("status")
        if status in {"unknown", "executing"}:
            return ActivityOutcome(
                status="unknown",
                failure_code="REMEDIATION_OUTCOME_UNKNOWN",
                result_ref=result_ref,
                public_data={"operation_id": result.get("actuator_operation_id")},
            )
        if status in {"failed", "cancelled"}:
            return ActivityOutcome(
                status="failed",
                failure_code=result.get("error_code") or "REMEDIATION_NOT_APPLIED",
                result_ref=result_ref,
            )
        if status not in {"executed", "verified"}:
            return ActivityOutcome.unknown(failure_code="REMEDIATION_RESULT_INVALID")
        return ActivityOutcome.succeeded(
            result_ref=result_ref,
            result_summary=str(result.get("status") or "executed"),
        )


__all__ = ["RemediationActivityHandler"]
