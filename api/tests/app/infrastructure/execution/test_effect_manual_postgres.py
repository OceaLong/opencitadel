# ruff: noqa: F401,F811 -- fixture dependencies imported for isolated PostgreSQL
"""Human recovery uses real PG roles, durable requests, FileService and disk bytes."""

import asyncio
import io
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from fastapi import UploadFile
from sqlalchemy import select, text, update

from app.application.execution.effect_reconciler import EffectReconciler
from app.application.services.file_service import FileService
from app.domain.errors import ConflictError, ForbiddenError
from app.domain.execution.aggregate import replay
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.effect import EffectState
from app.domain.execution.run import RunAggregate
from app.domain.execution.tool_contract import ToolApprovalSubject
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.file import File
from app.infrastructure.execution.models import ExecutionActivityTaskORM, ExecutionEventORM
from app.infrastructure.execution.postgres_activity_fence import ActivityLeaseExpiredError
from app.infrastructure.execution.postgres_effect_store import authorize_actor
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.models.file import FileModel
from app.infrastructure.observability.execution_metrics import ExecutionMetrics
from app.infrastructure.repositories.db_evaluation_budget_repository import (
    DBEvaluationBudgetRepository,
)
from app.infrastructure.repositories.db_uow import DBUnitOfWork
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.infrastructure.repositories.test_evaluation_budget_repository import demand
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.interfaces.endpoints.test_effect_api_postgres import effect_http

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


class DiskFiles:
    """Filesystem transport only; metadata, scoping and fixed reads are production code."""

    def __init__(self, root, uow):
        self.root, self.uow = root, uow

    async def upload_file(self, payload):
        data = payload.file.read()
        file = File(
            filename=payload.filename,
            key=str(uuid4()),
            content_digest=sha256(data).hexdigest(),
            object_identity=str(uuid4()),
            size=len(data),
            mime_type=payload.content_type,
            owner_user_id=payload.owner_user_id,
            team_id=payload.team_id,
        )
        (self.root / file.key).write_bytes(data)
        async with self.uow() as uow:
            await uow.file.save(file)
            await uow.commit()
        return file

    async def download_file(self, file_id):
        async with self.uow() as uow:
            file = await uow.file.get_by_id(file_id)
        if file is None or not file.content_available:
            raise FileNotFoundError("fixture evidence unavailable")
        return (self.root / file.key).open("rb"), file


@pytest.fixture
async def manual(effect_http, datasets, tmp_path):
    f = effect_http
    dataset_service = datasets[0]
    auth = AuthorizationContext.for_principal(f.principal, scope=f.scope)

    def uow():
        return dataset_service.uow_factory(auth)

    files = FileService(uow, DiskFiles(tmp_path, uow))
    f.service.files = f.producer.files = files
    f.files = files
    f.reconciler = EffectReconciler(service=f.producer)
    return f


async def evidence_file(f, *, result=None, **overrides):
    package = {
        "schema_version": 1,
        "operation_id": str(f.spec.operation_id),
        "contract_digest": f.spec.contract_digest,
        "arguments_digest": f.spec.arguments_digest,
        "target_digest": f.spec.target_digest,
        **(
            {"result": result}
            if result is not None
            else {
                "evidence": {
                    "content": "Provider audit record verified by the accountable operator"
                }
            }
        ),
        **overrides,
    }
    raw = json.dumps(package).encode()
    file = await f.files.upload_file(
        UploadFile(file=io.BytesIO(raw), filename="verification.json", size=len(raw)), f.scope
    )
    return {"ref": "file://" + file.id, "sha256": file.content_digest}


async def operation(f):
    return EffectState.model_validate(
        (
            await f.producer.store.get(
                operation_id=f.spec.operation_id, run_id=f.spec.run_id, scope=f.scope
            )
        )["body"]
    )


async def formal(f):
    async with f.producer.store.transaction() as session:
        events = await PostgresEventStore(
            session, event_registries={"run": RunAggregate().event_registry}
        ).load_stream("run", str(f.spec.run_id))
    return replay(RunAggregate(), events, stream_id=str(f.spec.run_id)).state, events


async def resolve(f, *, decision="applied", result=True, key=None):
    current = await operation(f)
    material = await evidence_file(f)
    output = (
        await evidence_file(f, result={"success": True, "value": "verified"}) if result else None
    )
    arguments = {
        "run_id": f.spec.run_id,
        "operation_id": f.spec.operation_id,
        "scope": f.scope,
        "principal": f.principal,
        "scope_reason": None,
        "expected_revision": current.revision,
        "idempotency_key": key or str(uuid4()),
        "decision": decision,
        "reason": "Verified original operation from provider evidence",
        "evidence_refs": [material],
        "result_evidence_ref": output,
        "attestation": {
            "source": "Provider audit",
            "action": "write_fixture",
            "target": f.spec.target_digest,
            "occurred_at": datetime.now(UTC).isoformat(),
            "method": "Compared immutable operation evidence",
            "responsible_actor": f.principal.user_id,
        },
    }
    accepted = await f.service.submit_resolution(**arguments)
    return accepted, arguments


async def request_status(f, accepted):
    return await f.service.get_request(
        run_id=f.spec.run_id,
        operation_id=f.spec.operation_id,
        request_id=accepted["request_id"],
        scope=f.scope,
        principal=f.principal,
        scope_reason=None,
    )


async def hold_unknown(f):
    identity = str(uuid5(NAMESPACE_URL, "manual-effect-proof:" + str(f.spec.run_id)))
    value = demand(f.scope, f.principal, batch=str(f.spec.run_id), slots=1)
    async with f.producer.store.transaction() as session:
        budget = DBEvaluationBudgetRepository(
            session, signing_secret=load_deployment_settings().database_authorization_signing_secret
        )
        await budget.reserve(identity, value)
        await budget.mark_unknown(identity, value)
    return identity


async def budget_snapshot(f, identity):
    async with f.producer.store.transaction() as session:
        reservation = (
            (
                await session.execute(
                    text("SELECT * FROM evaluation_budget_reservations WHERE call_identity=:id"),
                    {"id": identity},
                )
            )
            .mappings()
            .one()
        )
        buckets = (
            (await session.execute(text("SELECT * FROM evaluation_budget_buckets ORDER BY key")))
            .mappings()
            .all()
        )
        return dict(reservation), [dict(row) for row in buckets]


@pytest.mark.parametrize("effect_http", ["manual_only"], indirect=True)
async def test_human_verified_result_resumes_once_and_preserves_physical_usage_hold(manual):
    f = manual
    assert f.spec.capability.value == "manual_only"
    with pytest.raises(ConflictError, match="query capability"):
        await f.service.submit_query(
            run_id=f.spec.run_id,
            operation_id=f.spec.operation_id,
            scope=f.scope,
            principal=f.principal,
            scope_reason=None,
            expected_revision=3,
            idempotency_key="no-provider-query",
        )
    identity = await hold_unknown(f)
    before = await budget_snapshot(f, identity)
    accepted, submitted = await resolve(f)
    await f.reconciler.process_pending()
    effect = await operation(f)
    assert effect.state == "applied"
    assert effect.latest_receipt.source == "human_verified"
    assert (await request_status(f, accepted))["status"] == "completed"
    async with f.producer.store.transaction() as session:
        metrics = await ExecutionMetrics().refresh(session, now=datetime.now(UTC))
    assert metrics.effect_rows["applied"] == 1
    assert metrics.effect_manual_resolutions["applied"] == 1
    state, events = await formal(f)
    assert state.status == "running"
    assert sum(event.event_type == "ActivityCompleted" for event in events) == 1
    assert await budget_snapshot(f, identity) == before
    # Lost API acknowledgement: same request survives deleted source material.
    async with f.producer.store.transaction() as session:
        await session.execute(
            update(FileModel)
            .where(FileModel.id == submitted["evidence_refs"][0]["ref"][7:])
            .values(content_available=False)
        )
    replayed = await f.service.submit_resolution(**submitted)
    assert replayed["request_id"] == accepted["request_id"]
    await f.reconciler.process_pending()
    _, events = await formal(f)
    assert sum(event.event_type == "ActivityCompleted" for event in events) == 1
    assert await budget_snapshot(f, identity) == before


async def test_result_material_can_be_supplied_once_without_empty_success(manual):
    f = manual
    identity = await hold_unknown(f)
    before = await budget_snapshot(f, identity)
    accepted, _ = await resolve(f, result=False)
    await f.reconciler.process_pending()
    state, events = await formal(f)
    assert (await operation(f)).awaiting_result
    assert state.status == "waiting"
    assert state.wait_reason == "result_material_missing"
    assert not any(event.event_type == "ActivityCompleted" for event in events)
    assert (await request_status(f, accepted))["status"] == "completed"
    supplied, args = await resolve(f)
    await f.reconciler.process_pending()
    assert (await request_status(f, supplied))["status"] == "completed"
    assert not (await operation(f)).awaiting_result
    assert (await f.service.submit_resolution(**args))["request_id"] == supplied["request_id"]
    await f.reconciler.process_pending()
    state, events = await formal(f)
    assert state.status == "running"
    assert sum(event.event_type == "ActivityCompleted" for event in events) == 1
    assert await budget_snapshot(f, identity) == before


async def test_terminal_run_keeps_late_human_fact_without_revival(manual):
    f = manual
    handler = f.producer.store._orchestrator
    cancelled = await handler.handle(
        CommandEnvelope(
            command_id=uuid4(),
            command_type="CancelRun",
            command_schema_version=1,
            stream_type="run",
            stream_id=str(f.spec.run_id),
            owner_user_id=f.scope.user_id,
            team_id=None,
            correlation_id=f.spec.run_id,
            causation_id=None,
            issued_at=datetime.now(UTC),
            payload={"reason": "Operator cancelled while effect was unknown"},
        )
    )
    assert cancelled.status == "accepted"
    identity = await hold_unknown(f)
    before = await budget_snapshot(f, identity)
    accepted, _ = await resolve(f)
    await f.reconciler.process_pending()
    state, events = await formal(f)
    assert state.status == "cancelled"
    assert (await operation(f)).state == "applied"
    assert (await request_status(f, accepted))["status"] == "completed"
    cancel_index = next(i for i, event in enumerate(events) if event.event_type == "RunCancelled")
    assert not any(
        event.event_type in {"RunResumed", "ActivityCompleted"}
        for event in events[cancel_index + 1 :]
    )
    assert await budget_snapshot(f, identity) == before


async def test_abandon_preserves_unknown_fact_and_usage_hold(manual):
    f = manual
    identity = await hold_unknown(f)
    before = await budget_snapshot(f, identity)
    accepted = await f.service.submit_abandon(
        run_id=f.spec.run_id,
        operation_id=f.spec.operation_id,
        scope=f.scope,
        principal=f.principal,
        scope_reason=None,
        expected_revision=3,
        idempotency_key="stop-waiting",
        reason="Human stops active reconciliation",
    )
    await f.reconciler.process_pending()
    current = await operation(f)
    assert current.state == "unknown"
    assert current.abandoned
    assert (await request_status(f, accepted))["status"] == "completed"
    assert await budget_snapshot(f, identity) == before


async def test_revoked_requester_pauses_accepted_resolution_without_confirming_fact(
    manual, monkeypatch
):
    f = manual
    accepted, _ = await resolve(f)
    verify = AsyncMock(wraps=f.producer.verify_manual)
    persist = AsyncMock(wraps=f.producer.persist_observation)
    monkeypatch.setattr(f.producer, "verify_manual", verify)
    monkeypatch.setattr(f.producer, "persist_observation", persist)
    async with f.producer.store.transaction() as session:
        await session.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": f.principal.user_id},
        )
    await f.reconciler.process_pending()
    verify.assert_not_awaited()
    persist.assert_not_awaited()
    async with f.producer.store.transaction() as session:
        status = await session.scalar(
            text("SELECT status FROM execution_effect_requests WHERE request_id=:id"),
            {"id": accepted["request_id"]},
        )
        assert status == "paused"
    assert (await operation(f)).state == "unknown"
    # Regrant never automatically consumes the old authorization.
    async with f.producer.store.transaction() as session:
        await session.execute(
            text("UPDATE users SET token_version=token_version-1 WHERE id=:id"),
            {"id": f.principal.user_id},
        )
    await f.reconciler.process_pending()
    assert (await request_status(f, accepted))["status"] == "paused"
    assert (await operation(f)).state == "unknown"


async def test_deleted_evidence_is_rechecked_at_consumption(manual):
    f = manual
    accepted, args = await resolve(f)
    async with f.producer.store.transaction() as session:
        await session.execute(
            update(FileModel)
            .where(FileModel.id == args["evidence_refs"][0]["ref"][7:])
            .values(content_available=False)
        )
    await f.reconciler.process_pending()
    assert (await request_status(f, accepted))["status"] == "failed"
    assert (await operation(f)).state == "unknown"


async def test_conflicting_late_receipts_remain_quarantined_without_activity_completion(manual):
    f = manual
    await resolve(f, result=False)
    await f.reconciler.process_pending()
    original = await operation(f)
    assert original.awaiting_result
    proof = original.dispatch_proof
    context = SimpleNamespace(worker_id=proof.worker_id, claim_generation=proof.claim_generation)
    for outcome in ("not_applied", "applied"):
        receipt = await f.producer.persist_observation(
            f.spec,
            state=outcome,
            source="provider_receipt",
            evidence={"operation_id": str(f.spec.operation_id)},
            result={"success": True} if outcome == "applied" else None,
        )
        await f.producer.store.defer_observation(f.spec, context, receipt)
        await f.reconciler.process_pending()
        state, events = await formal(f)
        assert state.status == "waiting"
        assert state.activity_results == ()
        assert state.effect_operations[0].integrity_conflict
        assert not any(event.event_type == "ActivityCompleted" for event in events)
    with pytest.raises(ConflictError, match="quarantined"):
        await resolve(f)
    detail = await f.service.get_operation(
        run_id=f.spec.run_id,
        operation_id=f.spec.operation_id,
        scope=f.scope,
        principal=f.principal,
        scope_reason=None,
    )
    assert detail["allowed_actions"] == []
    assert detail["wait_reason"] == "external_evidence_conflict"
    async with f.producer.store.transaction() as session:
        metrics = await ExecutionMetrics().refresh(session, now=datetime.now(UTC))
        assert metrics.effect_integrity_conflict_rows == 1


async def test_expired_reconciler_permit_rolls_back_fact_task_and_inbox_before_commit(manual):
    f = manual
    accepted, submitted = await resolve(f)
    for _ in range(10):
        row = await f.producer.store.claim_request()
        assert row is not None
        if row["request_id"] == accepted["request_id"]:
            break
        await f.producer.store.finish_request(row, error_code="FIXTURE_PREVIOUS_QUERY")
    assert row["request_id"] == accepted["request_id"]
    packages, result = await f.producer.verify_manual(f.spec, row["body"], f.scope, f.principal)
    observation = await f.producer.persist_observation(
        f.spec,
        state="applied",
        source="human_verified",
        evidence={"operation_id": str(f.spec.operation_id), "packages": packages},
        result=result,
        receipt_id=row["request_id"],
    )
    command = CommandEnvelope(
        command_id=row["request_id"],
        command_type="ResolveActivityOutcome",
        command_schema_version=1,
        stream_type="run",
        stream_id=str(f.spec.run_id),
        owner_user_id=f.scope.user_id,
        team_id=None,
        correlation_id=f.spec.run_id,
        causation_id=row["request_id"],
        issued_at=row["created_at"],
        payload={
            "operation_id": str(f.spec.operation_id),
            "expected_revision": 3,
            "request_id": str(row["request_id"]),
            "observation": observation.model_dump(mode="json"),
        },
    )
    before, before_events = await formal(f)
    async with f.producer.store.transaction() as session:
        deadline = await session.scalar(
            text(
                "UPDATE execution_effect_requests SET claim_until=clock_timestamp()+interval '1 second' "
                "WHERE request_id=:id RETURNING claim_until"
            ),
            {"id": row["request_id"]},
        )
    reached_append = []

    class DelayedEventStore(PostgresEventStore):
        async def append(self, *args, **kwargs):
            reached_append.append(True)
            # This hook runs after real authority validation and waits on the
            # database's own deadline, never a mocked application clock.
            await self._session.execute(
                text(
                    "SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM "
                    "(CAST(:deadline AS timestamptz)-clock_timestamp())))+0.02)"
                ),
                {"deadline": deadline},
            )
            return await super().append(*args, **kwargs)

    handler = f.producer.store._orchestrator
    original_factory = handler._event_store_factory
    handler._event_store_factory = lambda session: DelayedEventStore(
        session, event_registries={"run": RunAggregate().event_registry}
    )
    try:
        with pytest.raises(ActivityLeaseExpiredError, match="effect authority"):
            await f.producer.store.finish_request(row, command=command)
    finally:
        handler._event_store_factory = original_factory
    assert reached_append
    current, events = await formal(f)
    assert current == before
    assert events == before_events
    assert (await operation(f)).state == "unknown"
    async with f.producer.store.transaction() as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM execution_command_inbox WHERE command_id=:id"),
                {"id": row["request_id"]},
            )
            == 0
        )
        assert (
            await session.scalar(
                text("SELECT count(*) FROM execution_effect_receipts WHERE receipt_id=:id"),
                {"id": row["request_id"]},
            )
            == 0
        )
    # The expired claim can be reclaimed, then commit the same request once.
    await f.reconciler.process_pending()
    assert (await request_status(f, accepted))["status"] == "completed"
    assert (await operation(f)).state == "applied"
    assert (await f.service.submit_resolution(**submitted))["request_id"] == row["request_id"]


async def test_reconciler_waiting_for_row_lock_cannot_fail_request_after_its_permit_expires(manual):
    f = manual
    accepted, _ = await resolve(f)
    for _ in range(10):
        row = await f.producer.store.claim_request()
        assert row is not None
        if row["request_id"] == accepted["request_id"]:
            break
        await f.producer.store.finish_request(row, error_code="FIXTURE_PREVIOUS_QUERY")
    assert row["request_id"] == accepted["request_id"]
    async with f.producer.store.transaction() as session:
        deadline = await session.scalar(
            text(
                "UPDATE execution_effect_requests SET claim_until=clock_timestamp()+interval '1 second' "
                "WHERE request_id=:id RETURNING claim_until"
            ),
            {"id": row["request_id"]},
        )
    task = None
    transaction = f.producer.store.transaction
    started = asyncio.Event()
    waiter = {}

    @asynccontextmanager
    async def observed_transaction(*args, **kwargs):
        async with transaction(*args, **kwargs) as session:
            waiter["pid"] = await session.scalar(text("SELECT pg_backend_pid()"))
            started.set()
            yield session

    try:
        async with f.producer.store.transaction() as holder:
            # Lock without modifying the row, so PostgreSQL cannot rely on a
            # changed-row recheck of the SELECT predicate after the lock wait.
            await holder.execute(
                text(
                    "SELECT request_id FROM execution_effect_requests WHERE request_id=:id FOR UPDATE"
                ),
                {"id": row["request_id"]},
            )
            f.producer.store.transaction = observed_transaction
            task = asyncio.create_task(
                f.producer.store.finish_request(row, error_code="ForbiddenError")
            )
            await asyncio.wait_for(started.wait(), timeout=5)
            for _ in range(100):
                waiting = await holder.scalar(
                    text("SELECT pg_backend_pid()=ANY(pg_blocking_pids(:pid))"),
                    {"pid": waiter["pid"]},
                )
                if waiting:
                    break
                await asyncio.sleep(0.01)
            assert waiting
            assert not task.done()
            await holder.execute(
                text(
                    "SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM "
                    "(CAST(:deadline AS timestamptz)-clock_timestamp())))+0.02)"
                ),
                {"deadline": deadline},
            )
        assert await asyncio.wait_for(task, timeout=5) is False
    finally:
        f.producer.store.transaction = transaction
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert (await request_status(f, accepted))["status"] == "running"
    assert (await operation(f)).state == "unknown"


async def test_not_applied_is_terminal_and_never_restarts_the_original_invocation(manual):
    f = manual
    accepted, _ = await resolve(f, decision="not_applied", result=False)
    await f.reconciler.process_pending()
    assert (await request_status(f, accepted))["status"] == "completed"
    assert (await operation(f)).state == "not_applied"
    state, events = await formal(f)
    assert (f.spec.activity_id, "failed", 0) in state.settled_activities
    assert sum(event.event_type == "EffectDispatchStarted" for event in events) == 1
    assert sum(event.event_type == "EffectOperationPrepared" for event in events) == 1
    assert not state.active_activity_ids
    await f.reconciler.process_pending()
    with pytest.raises(ConflictError, match="cannot be overwritten"):
        await resolve(f)
    _, after = await formal(f)
    assert len(after) == len(events)

    # A business retry is a distinct intent, with a fresh exact approval and
    # current authority. It cannot mutate or reuse the settled operation.
    new_activity, new_invocation, approval_id = uuid4(), uuid4(), uuid4()
    subject = ToolApprovalSubject(
        invocation_id=new_invocation,
        originating_activity_id=uuid4(),
        tool_call_id="call-2",
        tool_name="write_fixture",
        contract_ref="execution/contracts/fixture-v2.json",
        contract_object_digest="d" * 64,
        contract_digest=f.spec.contract_digest,
        arguments_digest=f.spec.arguments_digest,
        target_digest=f.spec.target_digest,
        scope_digest="e" * 64,
        authorization_epoch="f" * 64,
    )
    handler = f.producer.store._orchestrator

    async def submit(kind, payload, version):
        return await handler.handle(
            CommandEnvelope(
                command_id=uuid4(),
                command_type=kind,
                command_schema_version=version,
                stream_type="run",
                stream_id=str(f.spec.run_id),
                owner_user_id=f.scope.user_id,
                team_id=None,
                correlation_id=f.spec.run_id,
                causation_id=None,
                issued_at=datetime.now(UTC),
                payload=payload,
            )
        )

    assert (
        await submit(
            "RequestApproval",
            {
                "approval_id": str(approval_id),
                "subject_activity_id": str(new_activity),
                "approval_kind": "tool_call",
                "risk_summary": "New business attempt",
                "subject_label": "write_fixture",
                "subject": subject.model_dump(mode="json"),
                "subject_digest": subject.digest,
            },
            2,
        )
    ).status == "accepted"
    decision = {
        "approval_id": str(approval_id),
        "decision": "approved",
        "actor_user_id": f.principal.user_id,
        "expected_subject_digest": "0" * 64,
    }
    assert (await submit("DecideApproval", decision, 2)).status == "rejected"
    decision["expected_subject_digest"] = subject.digest
    assert (await submit("DecideApproval", decision, 2)).status == "accepted"
    assert (
        await submit(
            "RequestActivity",
            {
                "activity_id": str(new_activity),
                "activity_type": "tool.call",
                "timeout_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "input_digest": "a" * 64,
                "input_payload": {"subject": subject.model_dump(mode="json")},
                "invocation_id": str(new_invocation),
                "subject_digest": subject.digest,
            },
            3,
        )
    ).status == "accepted"
    async with f.producer.store.transaction() as session:
        await session.execute(
            update(ExecutionActivityTaskORM)
            .where(ExecutionActivityTaskORM.activity_id == new_activity)
            .values(
                status="call_started",
                claim_generation=1,
                claimed_by="new-intent-worker",
                claim_deadline=datetime.now(UTC) + timedelta(minutes=5),
                call_started_at=datetime.now(UTC),
            )
        )
    settings = load_deployment_settings()
    f.producer.store._uow_factory = lambda: DBUnitOfWork(
        f.sessions,
        secret_cipher=ApiKeyCipher("test"),
        audit_signing_key="test",
        audit_signing_key_id="test",
        database_authorization_signing_secret=settings.database_authorization_signing_secret,
        authorization_context=f.auth,
    )
    new_spec = f.spec.model_copy(
        update={
            "operation_id": new_invocation,
            "invocation_id": new_invocation,
            "activity_id": new_activity,
            "idempotency_key": f"inv:{new_invocation}",
            "provider_locator": {"tool_name": "write_fixture", "tool_call_id": "call-2"},
        }
    )
    context = SimpleNamespace(
        run=SimpleNamespace(run_id=f.spec.run_id, owner_scope=f.scope),
        activity_id=new_activity,
        generation=0,
        claim_generation=1,
        worker_id="new-intent-worker",
        require_active_lease=None,
    )
    sends = []
    current_principal = f.principal

    async def preflight(uow):
        await authorize_actor(uow.db_session, f.scope, current_principal)

    async def send():
        sends.append(new_spec.operation_id)
        return {"success": True, "value": "new attempt only"}

    async with f.producer.store.transaction() as session:
        await session.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": f.principal.user_id},
        )
    with pytest.raises(ForbiddenError):
        await f.producer.invoke(new_spec, context, send, preflight=preflight)
    assert sends == []
    _, blocked_events = await formal(f)
    assert sum(event.event_type == "EffectOperationPrepared" for event in blocked_events) == 1
    current_principal = f.principal.model_copy(
        update={"token_version": f.principal.token_version + 1}
    )
    assert (
        await f.producer.invoke(new_spec, context, send, preflight=preflight)
    ).state == "applied"
    assert sends == [new_invocation]
    assert (await operation(f)).state == "not_applied"
    assert (
        await f.producer.invoke(new_spec, context, send, preflight=preflight)
    ).state == "applied"
    assert sends == [new_invocation]
    _, final_events = await formal(f)
    assert sum(event.event_type == "EffectOperationPrepared" for event in final_events) == 2
    assert sum(event.event_type == "EffectDispatchStarted" for event in final_events) == 2
