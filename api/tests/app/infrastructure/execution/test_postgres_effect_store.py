"""Real PostgreSQL proof: effects commit with their Run and restore original tasks."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.effect_reconciler import EffectReconciler
from app.application.execution.effect_service import EffectService, command_envelope
from app.domain.execution.commands import ActivityLeaseProof, CommandEnvelope
from app.domain.execution.effect import EffectOperationSpec, EffectState
from app.domain.execution.run import RunAggregate
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.infrastructure.execution.effect_models import operations, receipts
from app.infrastructure.execution.models import ExecutionActivityTaskORM, ExecutionEventORM
from app.infrastructure.execution.postgres_effect_store import PostgresEffectStore
from app.infrastructure.execution.postgres_recovery import PostgresRecoveryWorker
from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_kernel_database_uri,
    run_policy_snapshot_json,
)


@pytest.mark.asyncio
@pytest.mark.usefixtures("postgres_integration")
async def test_late_receipt_recovers_same_unknown_activity_atomically_and_indexes_rebuild():
    settings = load_deployment_settings()
    engine = create_async_engine(execution_kernel_database_uri())
    sessions = authenticated_session_factory(
        engine, signing_secret=settings.database_authorization_signing_secret
    )
    authorization = AuthorizationContext.system("execution-kernel")
    run_id, activity_id, operation_id = uuid4(), uuid4(), uuid4()
    owner = f"effect-test-{uuid4()}"
    scope = OwnerScope.personal(owner)
    orchestrator = SqlAlchemyExecutionOrchestrator(
        session_factory=sessions, aggregates={"run": RunAggregate()}, authorization=authorization
    )
    store = PostgresEffectStore(
        session_factory=sessions, authorization=authorization, orchestrator=orchestrator
    )
    now = datetime.now(UTC)
    proof = ActivityLeaseProof(
        activity_id=activity_id, request_generation=0, claim_generation=1, worker_id="effect-proof"
    )

    async def send(name, payload, *, version=1, worker=False):
        envelope = CommandEnvelope(
            command_id=uuid4(),
            command_type=name,
            command_schema_version=version,
            stream_type="run",
            stream_id=str(run_id),
            owner_user_id=owner,
            team_id=None,
            correlation_id=run_id,
            causation_id=None,
            issued_at=datetime.now(UTC),
            payload=payload,
            activity_lease_proof=proof if worker else None,
        )
        result = await orchestrator.handle(envelope)
        assert result.status == "accepted", result
        return result

    memory = {}

    async def put(key, value):
        memory[key] = value

    async def get(key):
        return memory[key]

    service = EffectService(
        store=store,
        objects=ActivityObjectStore(SimpleNamespace(put_bytes=put, get_bytes=get)),
        files=None,
    )
    spec = EffectOperationSpec(
        operation_id=operation_id,
        invocation_id=operation_id,
        run_id=run_id,
        activity_id=activity_id,
        generation=0,
        provider_identity="manual-provider",
        contract_digest="a" * 64,
        arguments_digest="b" * 64,
        target_digest="c" * 64,
        idempotency_key=f"inv:{operation_id}",
        capability="manual_only",
        output_schema={"type": "object"},
    )
    context = SimpleNamespace(
        worker_id="effect-proof", claim_generation=1, run=SimpleNamespace(owner_scope=scope)
    )
    try:
        await send(
            "CreateRun",
            {
                "family": "agent",
                "source_entity_type": "effect-proof",
                "source_entity_id": str(run_id),
                "semantic_payload": {},
                "policy_snapshot": run_policy_snapshot_json("agent"),
            },
        )
        await send("StartRun", {})
        await send(
            "RequestActivity",
            {
                "activity_id": str(activity_id),
                "activity_type": "tool.call",
                "timeout_at": (now + timedelta(hours=1)).isoformat(),
                "input_digest": "a" * 64,
            },
            version=3,
        )
        # Own-row fixture establishes a real current lease; the production
        # command authorizer must query/fence these DB values rather than trust us.
        async with sessions() as session:
            await configure_session_authorization(session, authorization)
            await session.execute(
                update(ExecutionActivityTaskORM)
                .where(ExecutionActivityTaskORM.activity_id == activity_id)
                .values(
                    status="call_started",
                    claim_generation=1,
                    claimed_by="effect-proof",
                    claim_deadline=now + timedelta(minutes=5),
                    call_started_at=now,
                )
            )
            await session.commit()
        await store.worker_command(
            command_envelope(
                spec=spec,
                name="PrepareEffectOperation",
                payload={"spec": spec.model_dump(mode="json")},
                context=context,
            )
        )
        await store.worker_command(
            command_envelope(
                spec=spec,
                name="StartEffectDispatch",
                payload={
                    "operation_id": str(operation_id),
                    "expected_revision": 1,
                    "dispatch_proof": proof.model_dump(mode="json"),
                },
                context=context,
            )
        )
        unknown = await service.persist_observation(
            spec,
            state="unknown",
            source="transport_unknown",
            evidence={"operation_id": str(operation_id), "error": "lost response"},
        )
        await store.worker_command(
            command_envelope(
                spec=spec,
                name="RecordEffectObservation",
                payload={
                    "operation_id": str(operation_id),
                    "expected_revision": 2,
                    "observation": unknown.model_dump(mode="json"),
                },
                context=context,
            )
        )
        await send(
            "MarkActivityOutcomeUnknown",
            {
                "activity_id": str(activity_id),
                "generation": 0,
                "claim_generation": 1,
                "failure_code": "EXTERNAL_OUTCOME_UNKNOWN",
            },
            version=2,
            worker=True,
        )
        # The old worker may retain evidence, but has no event append authority.
        applied = await service.persist_observation(
            spec,
            state="applied",
            source="provider_receipt",
            evidence={"operation_id": str(operation_id), "response": {"success": True}},
            result={"success": True},
        )
        late = await orchestrator.handle(
            command_envelope(
                spec=spec,
                name="RecordEffectObservation",
                payload={
                    "operation_id": str(operation_id),
                    "expected_revision": 3,
                    "observation": applied.model_dump(mode="json"),
                },
                context=context,
            )
        )
        assert late.status == "rejected"
        await store.defer_observation(spec, context, applied)
        reconciler = EffectReconciler(service=service)
        await reconciler.process_pending()
        state = EffectState.model_validate((await store.get(operation_id=operation_id))["body"])
        assert state.state == "applied"
        async with sessions() as session:
            await configure_session_authorization(session, authorization)
            task = await session.get(ExecutionActivityTaskORM, activity_id)
            assert task.status == "succeeded"
            event_types = list(
                await session.scalars(
                    select(ExecutionEventORM.event_type)
                    .where(ExecutionEventORM.stream_id == str(run_id))
                    .order_by(ExecutionEventORM.position)
                )
            )
            assert event_types[-1] == "RunResumed"
            assert event_types.count("ActivityCompleted") == 1
            saved_receipts = list(
                await session.scalars(
                    select(receipts.c.receipt_id).where(receipts.c.operation_id == operation_id)
                )
            )
            assert len(saved_receipts) == 2
        # Recovery must discover the authoritative stream even when both
        # derived indexes have disappeared, before clearing scope quarantine.
        recovery = PostgresRecoveryWorker(
            session_factory=sessions,
            authorization=authorization,
            audit_signing_key=settings.audit_signing_key,
            audit_signing_key_id=settings.audit_signing_key_id,
        )
        await recovery.mark(scope)
        async with store.transaction() as session:
            await session.execute(delete(receipts).where(receipts.c.operation_id == operation_id))
            await session.execute(
                delete(operations).where(operations.c.operation_id == operation_id)
            )
        await recovery.clear(scope)
        rebuilt = EffectState.model_validate((await store.get(operation_id=operation_id))["body"])
        assert rebuilt == state
        await reconciler.process_pending()
        async with sessions() as session:
            await configure_session_authorization(session, authorization)
            assert (
                await session.scalar(
                    select(operations.c.revision).where(operations.c.operation_id == operation_id)
                )
                == state.revision
            )
    finally:
        await engine.dispose()
