"""Production adapters for external operation evidence and durable recovery."""

from app.application.execution.effect_reconciler import EffectReconciler
from app.application.execution.effect_service import EffectService
from app.domain.execution.effect import EffectState
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.postgres_effect_store import PostgresEffectStore
from app.infrastructure.execution.postgres_run_context_source import PostgresRunContextSource


def build_effect_service(*, resources, shared):
    store = PostgresEffectStore(
        session_factory=resources.postgres.session_factory, uow_factory=shared.uow_factory
    )
    return EffectService(store=store, objects=shared.activity_objects, files=shared.file_service)


def build_effect_reconciler(*, resources, shared, service):
    contexts = PostgresRunContextSource(
        session_factory=resources.postgres.session_factory,
        authorization=AuthorizationContext.system("execution-kernel"),
    )

    async def authority(spec, scope):
        async with shared.uow_factory() as uow:
            await uow.execution_usage.invocation_authority(scope, spec.run_id, lock=True)

    async def patrol_resolve(spec, scope):
        context = await contexts.load(spec.run_id)
        value = await shared.patrol_remediation_service.resolve_operation(
            str(spec.operation_id),
            scope,
            policy=context.policy_snapshot.common.activity,
            persist_outcome=False,
        )
        receipt = value.get("actuator_receipt") or {}
        state = receipt.get("state", "unknown")
        return {
            "state": state,
            "evidence": {"operation_id": str(spec.operation_id), "provider_receipt": receipt},
            "result": value
            if state == "applied" and isinstance(value.get("after_observation"), dict)
            else None,
        }

    async def after_commit(spec, scope):
        if spec.provider_identity != "ops-actuator":
            return
        operation = EffectState.model_validate(
            (await service.store.get(operation_id=spec.operation_id))["body"]
        )
        if operation.integrity_conflict:
            await shared.patrol_remediation_service.apply_effect_fact(
                str(spec.operation_id), scope, state="integrity_conflict", result=None, receipt=None
            )
            return
        result = (
            await service.objects.load_result(operation.result_ref)
            if operation.result_ref
            else None
        )
        evidence = (
            await service.objects.load_result(operation.latest_receipt.evidence.ref)
            if operation.latest_receipt
            else {}
        )
        receipt = evidence.get("provider_receipt") or (evidence.get("response") or {}).get(
            "actuator_receipt"
        )
        await shared.patrol_remediation_service.apply_effect_fact(
            str(spec.operation_id), scope, state=operation.state, result=result, receipt=receipt
        )

    return EffectReconciler(
        service=service,
        resolvers={"ops-actuator": patrol_resolve},
        authority=authority,
        after_commit=after_commit,
    )
