# ruff: noqa: F401,F811 -- shared isolated PostgreSQL fixture dependencies
"""One real tool.call handler recovers according to each frozen Invocation.

The trusted test catalog counts its physical boundary in PostgreSQL and loses
write responses deliberately. This proves kernel recovery and persistence;
actual remote transport and Kubernetes receipts have separate adapter/G4 tests.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update

from app.application.execution.activities.tool_call import ToolCallActivityHandler
from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.activity_registry import create_activity_registry
from app.application.execution.activity_worker import ActivityWorker
from app.application.execution.effect_reconciler import EffectReconciler
from app.application.execution.effect_service import EffectService
from app.application.execution.invocation_contracts import finalize_subject
from app.application.execution.run_service import RunService
from app.domain.evaluation.configuration import digest
from app.domain.execution.activity import ActivityContext
from app.domain.execution.tool_contract import (
    EffectContract,
    RecordedContract,
    ToolApprovalSubject,
    execution_contract,
)
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.domain.services.tools.capability_policy import EXTERNAL_WRITE, READ_SAFE
from app.infrastructure.execution.models import ExecutionActivityTaskORM, ExecutionEventORM
from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
from app.infrastructure.execution.postgres_effect_store import PostgresEffectStore
from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
from app.infrastructure.execution.postgres_run_context_source import PostgresRunContextSource
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.execution_database_fixture import isolated_execution_database
from tests.app.execution_test_support import execution_admin_session
from tests.app.integration.test_phase1_activity_fencing import (
    AUTH,
    envelope,
    physical_calls,
    runtime,
    seed,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("isolated_execution_database")]


@pytest.mark.parametrize("kind", ["read", "dedupe", "native_write", "mcp", "a2a"])
async def test_one_tool_handler_recovers_each_frozen_invocation_without_blind_write(runtime, kind):
    sessions, orchestrator = runtime
    run, owner, _ = await seed(runtime, count=0)
    kernel = AuthorizationContext.system("execution-kernel")
    orchestrator._authorization = kernel
    scope = OwnerScope.personal(owner)
    activity = uuid4()
    blobs = {}

    async def put(key, value):
        blobs[key] = value

    async def get(key):
        return blobs[key]

    objects = ActivityObjectStore(SimpleNamespace(put_bytes=put, get_bytes=get))
    contract = execution_contract(
        RecordedContract(
            name="fixture_call",
            pack=kind if kind in {"mcp", "a2a"} else "fixture",
            schema_body={
                "type": "function",
                "function": {
                    "name": "fixture_call",
                    "parameters": {
                        "type": "object",
                        "properties": {"key": {"type": "string"}},
                        "required": ["key"],
                    },
                },
            },
            policy=READ_SAFE if kind == "read" else EXTERNAL_WRITE,
            binding_revision="fixed-fixture-v1",
            authority_revision="fixed-fixture-v1",
        )
    )
    if kind == "dedupe":
        contract = contract.model_copy(
            update={
                "effect_contract": EffectContract(
                    capability="dedupe_resolve",
                    resolve=True,
                    key_scope="fixture-provider",
                    retention_seconds=86400,
                )
            }
        )
    contract_ref, contract_object_digest = await objects.put_input(
        run, contract.model_dump(mode="json")
    )
    arguments = {"key": "owned"}
    subject = finalize_subject(
        ToolApprovalSubject(
            invocation_id=uuid4(),
            originating_activity_id=uuid4(),
            tool_call_id="call-1",
            tool_name=contract.name,
            contract_ref=contract_ref,
            contract_object_digest=contract_object_digest,
            contract_digest=contract.digest,
            arguments_digest=digest(arguments),
            target_digest="a" * 64,
            scope_digest=digest(scope.model_dump(mode="json")),
            authorization_epoch="b" * 64,
        ),
        arguments,
    )
    input_ref, input_digest = await objects.put_input(run, {"mode": "agent"})

    async def send(name, payload, *, version=1, proof=None):
        command = envelope(run, owner, name, payload, proof=proof).model_copy(
            update={"command_schema_version": version}
        )
        result = await orchestrator.handle(command)
        assert result.status == "accepted", result

    if kind != "read":
        approval = uuid4()
        await send(
            "RequestApproval",
            {
                "approval_id": str(approval),
                "subject_activity_id": str(activity),
                "approval_kind": "tool_effect",
                "risk_summary": "Owned recovery fixture",
                "subject_label": contract.name,
                "subject": subject.model_dump(mode="json"),
                "subject_digest": subject.digest,
            },
            version=2,
        )
        await send(
            "DecideApproval",
            {
                "approval_id": str(approval),
                "decision": "approved",
                "actor_user_id": owner,
                "expected_subject_digest": subject.digest,
            },
            version=2,
        )
    await send(
        "RequestActivity",
        {
            "activity_id": str(activity),
            "activity_type": "tool.call",
            "invocation_id": str(subject.invocation_id),
            "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "input_ref": input_ref,
            "input_digest": input_digest,
            "subject_digest": subject.digest,
            "input_payload": {
                "tool_call": {"call_id": "call-1", "name": contract.name, "arguments": arguments},
                "subject": subject.model_dump(mode="json"),
            },
        },
        version=3,
    )
    await PostgresFormalProjector(session_factory=sessions, authorization=AUTH).run_once(
        scope, limit=1000, notify=False
    )
    store = PostgresActivityStore(session_factory=sessions, authorization=AUTH)
    contexts = PostgresRunContextSource(session_factory=sessions, authorization=AUTH)
    effects = EffectService(
        store=PostgresEffectStore(
            session_factory=sessions,
            authorization=kernel,
            orchestrator=orchestrator,
        ),
        objects=objects,
        files=None,
    )
    discoveries = []

    class Catalog:
        async def invoke(self, payload, context, *, invocation, contract, execute=None, **kwargs):
            discoveries.append(context.claim_generation)

            async def physical_send():
                key = (
                    f"inv:{invocation.invocation_id}" if kind != "read" else context.idempotency_key
                )
                async with execution_admin_session() as db:
                    await db.execute(
                        text(
                            "INSERT INTO phase1_physical_calls(activity_id,worker_id,operation_key) VALUES(:id,:worker,:key)"
                        ),
                        {"id": activity, "worker": context.worker_id, "key": key},
                    )
                    await db.commit()
                if kind != "read":
                    raise TimeoutError("physical write applied; original response lost")
                return {"success": True, "data": "read value"}

            return await execute(physical_send, None) if execute else await physical_send()

    handler = ToolCallActivityHandler(objects=objects, tools=Catalog(), effects=effects)
    assert not handler.idempotent
    (claim,) = await store.claim(
        now=datetime.now(UTC), limit=1, worker_id="old", claim_ttl=timedelta(seconds=30)
    )
    assert await store.mark_call_started(claim, now=datetime.now(UTC))
    await send(
        "MarkActivityCallStarted",
        {
            "activity_id": str(activity),
            "generation": 0,
            "claim_generation": claim.claim_generation,
        },
        version=2,
        proof=claim.lease_proof,
    )
    original = await handler.execute(
        claim.request,
        ActivityContext(
            activity_id=activity,
            generation=0,
            worker_id="old",
            claim_generation=claim.claim_generation,
            idempotency_key=str(activity),
            owner_user_id=owner,
            team_id=None,
            run=await contexts.load(run),
        ),
    )
    assert original.status == ("succeeded" if kind == "read" else "unknown")
    # The first worker loses ownership before submitting its Activity outcome.
    async with execution_admin_session() as db:
        await db.execute(
            update(ExecutionActivityTaskORM)
            .where(ExecutionActivityTaskORM.activity_id == activity)
            .values(claim_deadline=func.clock_timestamp() - timedelta(seconds=1))
        )
        await db.commit()
    worker = ActivityWorker(
        store=store,
        run_contexts=contexts,
        run_service=RunService(orchestrator=orchestrator),
        registry=create_activity_registry(handler),
        worker_id="replacement",
    )
    stats = await worker.run_once(now=datetime.now(UTC), limit=1)
    calls = await physical_calls()
    assert len(calls) == (2 if kind == "read" else 1)
    assert stats.succeeded == (1 if kind == "read" else 0)
    assert stats.unknown == (0 if kind == "read" else 1)
    assert discoveries == ([1, 2] if kind == "read" else [1])
    if kind != "read":
        saved = await effects.store.get(operation_id=subject.invocation_id)
        assert saved["body"]["state"] == "unknown"
        assert saved["body"]["spec"]["idempotency_key"] == calls[0]["operation_key"]
        assert saved["body"]["spec"]["capability"] == (
            "dedupe_resolve" if kind == "dedupe" else "manual_only"
        )
        queried = []

        async def resolve(spec, owner_scope):
            queried.append(spec.idempotency_key)
            assert owner_scope == scope
            return {
                "state": "applied",
                "evidence": {"operation_id": str(spec.operation_id)},
                "result": {"success": True, "data": "original durable receipt"},
            }

        async with effects.store.transaction() as db:
            await db.execute(
                text(
                    "UPDATE execution_effect_requests SET due_at=clock_timestamp() WHERE operation_id=:id"
                ),
                {"id": subject.invocation_id},
            )
        reconciler = EffectReconciler(service=effects, resolvers={contract.pack: resolve})
        await reconciler.process_pending()
        assert queried == ([calls[0]["operation_key"]] if kind == "dedupe" else [])
        assert len(await physical_calls()) == 1
        saved = await effects.store.get(operation_id=subject.invocation_id)
        assert saved["body"]["state"] == ("applied" if kind == "dedupe" else "unknown")
    async with execution_admin_session() as db:
        completions = await db.scalar(
            select(func.count())
            .select_from(ExecutionEventORM)
            .where(
                ExecutionEventORM.stream_id == str(run),
                ExecutionEventORM.event_type == "ActivityCompleted",
            )
        )
        assert completions == (1 if kind in {"read", "dedupe"} else 0)
