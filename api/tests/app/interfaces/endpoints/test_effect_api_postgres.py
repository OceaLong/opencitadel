# ruff: noqa: F401,F811 -- imported PostgreSQL fixture dependencies
"""Real API-login effect routes; no mocked service or operation repository."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.effect_reconciler import EffectReconciler
from app.application.execution.effect_service import EffectService, command_envelope
from app.domain.execution.commands import ActivityLeaseProof, CommandEnvelope
from app.domain.execution.effect import EffectOperationSpec
from app.domain.execution.run import RunAggregate
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, WorkspaceContext
from app.domain.models.team import TeamRole
from app.domain.models.user import GlobalRole
from app.infrastructure.execution.models import ExecutionActivityTaskORM
from app.infrastructure.execution.postgres_effect_store import PostgresEffectStore
from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
from app.infrastructure.observability.execution_metrics import ExecutionMetrics
from app.infrastructure.security.db_authorization import configure_session_authorization
from app.interfaces.auth_dependencies import get_current_principal, get_workspace_context
from app.interfaces.endpoints.execution_effect_routes import router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_effect_service
from core.config import load_deployment_settings
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_kernel_database_uri,
    run_policy_snapshot_json,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_query_reclaims_consume_the_operation_query_budget(effect_http):
    f = effect_http
    accepted = await f.service.submit_query(
        run_id=f.spec.run_id,
        operation_id=f.spec.operation_id,
        scope=f.scope,
        principal=f.principal,
        scope_reason=None,
        expected_revision=3,
        idempotency_key="query-recovery-budget",
    )
    for attempt in range(1, f.spec.query_limit + 1):
        claimed = await f.producer.store.claim_request()
        assert claimed["request_id"] == accepted["request_id"]
        assert claimed["attempts"] == attempt
        # Crash after a possible send, before its response is durably recorded.
        async with f.producer.store.transaction() as session:
            await session.execute(
                text(
                    "UPDATE execution_effect_requests SET claim_until=clock_timestamp()-interval '1 second' WHERE request_id=:id"
                ),
                {"id": claimed["request_id"]},
            )
    assert await f.producer.store.query_count(f.spec.operation_id) == f.spec.query_limit
    resolver = AsyncMock(return_value={"state": "unknown", "evidence": {}})
    await EffectReconciler(
        service=f.producer, resolvers={"controlled-provider": resolver}
    ).process_pending()
    resolver.assert_not_awaited()
    async with f.producer.store.transaction() as session:
        assert (
            await session.scalar(
                text("SELECT status FROM execution_effect_requests WHERE request_id=:id"),
                {"id": accepted["request_id"]},
            )
            == "failed"
        )


@pytest.fixture
async def effect_http(datasets, isolated_database, request):
    dataset_service, scope, principal, _, _ = datasets
    options = request.param if isinstance(getattr(request, "param", None), dict) else {}
    settings = load_deployment_settings()
    engine = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=isolated_database[0].url.database)
    )
    sessions = authenticated_session_factory(
        engine, signing_secret=settings.database_authorization_signing_secret
    )
    auth = AuthorizationContext.system("execution-kernel")
    if getattr(request, "param", None) == "team":
        team_id = str(uuid4())
        async with sessions() as session:
            await configure_session_authorization(session, auth)
            await session.execute(
                text("INSERT INTO teams(id,name) VALUES(:id,'Effects proof')"), {"id": team_id}
            )
            await session.execute(
                text("INSERT INTO team_members(team_id,user_id,role) VALUES(:team,:user,'owner')"),
                {"team": team_id, "user": principal.user_id},
            )
            await session.commit()
        scope = OwnerScope.team(principal.user_id, team_id)
        principal.team_roles[team_id] = TeamRole.OWNER
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=sessions, aggregates={"run": RunAggregate()}, authorization=auth
    )
    kernel_store = PostgresEffectStore(
        session_factory=sessions, authorization=auth, orchestrator=handler
    )
    storage = {}

    async def put(key, value):
        storage[key] = value

    async def get(key):
        return storage[key]

    objects = ActivityObjectStore(SimpleNamespace(put_bytes=put, get_bytes=get))
    producer = EffectService(store=kernel_store, objects=objects, files=None)
    run_id, activity_id, operation_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    proof = ActivityLeaseProof(
        activity_id=activity_id,
        request_generation=0,
        claim_generation=1,
        worker_id="api-effect-fixture",
    )
    spec = EffectOperationSpec(
        operation_id=operation_id,
        invocation_id=operation_id,
        run_id=run_id,
        activity_id=activity_id,
        generation=0,
        provider_identity="controlled-provider",
        provider_locator={"tool_name": "write_fixture", "tool_call_id": "call-1"},
        contract_digest="a" * 64,
        arguments_digest="b" * 64,
        target_digest="c" * 64,
        idempotency_key=f"inv:{operation_id}",
        capability="manual_only"
        if getattr(request, "param", None) == "manual_only"
        else "dedupe_resolve",
    )
    context = SimpleNamespace(
        worker_id=proof.worker_id, claim_generation=1, run=SimpleNamespace(owner_scope=scope)
    )

    async def send(name, payload, version=1, worker=False):
        result = await handler.handle(
            CommandEnvelope(
                command_id=uuid4(),
                command_type=name,
                command_schema_version=version,
                stream_type="run",
                stream_id=str(run_id),
                owner_user_id=None if scope.team_id else scope.user_id,
                team_id=scope.team_id,
                correlation_id=run_id,
                causation_id=None,
                issued_at=datetime.now(UTC),
                payload=payload,
                activity_lease_proof=proof if worker else None,
            )
        )
        assert result.status == "accepted", result

    semantic = {}
    if options.get("private_input") is not None:
        input_ref, input_digest = await objects.put_input(run_id, options["private_input"])
        semantic = {"input_ref": input_ref, "input_digest": input_digest}
    await send(
        "CreateRun",
        {
            "family": "agent",
            "source_entity_type": options.get("source_entity_type", "effect-api-proof"),
            "source_entity_id": options.get("source_entity_id", str(run_id)),
            "semantic_payload": semantic,
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
        3,
    )
    async with sessions() as session:
        await configure_session_authorization(session, auth)
        await session.execute(
            update(ExecutionActivityTaskORM)
            .where(ExecutionActivityTaskORM.activity_id == activity_id)
            .values(
                status="call_started",
                claim_generation=1,
                claimed_by=proof.worker_id,
                claim_deadline=now + timedelta(minutes=5),
                call_started_at=now,
            )
        )
        await session.commit()
    for name, payload in (
        ("PrepareEffectOperation", {"spec": spec.model_dump(mode="json")}),
        (
            "StartEffectDispatch",
            {
                "operation_id": str(operation_id),
                "expected_revision": 1,
                "dispatch_proof": proof.model_dump(mode="json"),
            },
        ),
    ):
        await kernel_store.worker_command(
            command_envelope(spec=spec, name=name, payload=payload, context=context)
        )
    observation = await producer.persist_observation(
        spec,
        state="unknown",
        source="transport_unknown",
        evidence={"operation_id": str(operation_id), "error": "response loss"},
    )
    await kernel_store.worker_command(
        command_envelope(
            spec=spec,
            name="RecordEffectObservation",
            payload={
                "operation_id": str(operation_id),
                "expected_revision": 2,
                "observation": observation.model_dump(mode="json"),
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
        2,
        True,
    )
    api_sessions = dataset_service.uow_factory().session_factory
    service = EffectService(
        store=PostgresEffectStore(
            session_factory=api_sessions, uow_factory=dataset_service.uow_factory
        ),
        objects=objects,
        files=None,
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api")
    holder = {"scope": scope}
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        principal=principal, scope=holder["scope"]
    )
    app.dependency_overrides[get_effect_service] = lambda: service
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            yield SimpleNamespace(
                http=http,
                base=f"/api/execution-runs/{run_id}/effects",
                spec=spec,
                principal=principal,
                scope=scope,
                holder=holder,
                sessions=sessions,
                auth=auth,
                producer=producer,
                service=service,
                send=send,
                stored_objects=storage,
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("observed_state", ["unknown", "pending"])
async def test_effect_metrics_survive_collector_restart_and_distinguish_query_infrastructure(
    effect_http,
    observed_state,
):
    f = effect_http

    async def snapshot():
        async with f.producer.store.transaction() as session:
            return await ExecutionMetrics().refresh(session, now=datetime.now(UTC))

    before = await snapshot()
    assert before.effect_rows["unknown"] == 1
    assert before.effect_oldest_age_seconds["unknown"] >= 0
    assert before.effect_manual_resolutions["applied"] == 0
    args = {
        "run_id": f.spec.run_id,
        "operation_id": f.spec.operation_id,
        "scope": f.scope,
        "principal": f.principal,
        "scope_reason": None,
        "expected_revision": 3,
    }
    await f.service.submit_query(**args, idempotency_key="metrics-unavailable")

    async def unavailable(spec, scope):
        raise OSError("controlled resolver unavailable")

    worker = EffectReconciler(service=f.producer, resolvers={"controlled-provider": unavailable})
    await worker.process_pending()
    failed = await snapshot()
    assert failed.effect_resolution_unavailable_rows == 1
    assert failed.effect_rows["unknown"] == 1
    assert failed.effect_query_observations["not_applied"] == 0
    await f.service.submit_query(**args, idempotency_key="metrics-query")

    async def unknown(spec, scope):
        return {"state": observed_state, "evidence": {"operation_id": str(spec.operation_id)}}

    worker.resolvers["controlled-provider"] = unknown
    await worker.process_pending()
    durable = await snapshot()
    assert durable.effect_request_rows["query:completed"] == 1
    assert durable.effect_query_observations[observed_state] == 1
    assert durable.effect_rows[observed_state] == 1
    assert durable.effect_resolution_unavailable_rows == 1
    restarted = await snapshot()
    assert restarted.effect_query_observations == durable.effect_query_observations
    assert restarted.effect_request_rows == durable.effect_request_rows
    assert restarted.effect_resolution_unavailable_rows == 1


async def test_real_api_role_reads_and_enqueues_idempotent_effect_requests(effect_http):
    f = effect_http
    path = f"{f.base}/{f.spec.operation_id}"
    rows = await f.http.get(f.base)
    assert rows.status_code == 200, rows.text
    assert rows.json()["data"][0]["state"] == "unknown"
    detail = await f.http.get(path)
    assert detail.status_code == 200, detail.text
    assert detail.headers["cache-control"] == "no-store"
    assert detail.json()["data"]["allowed_actions"] == ["query", "resolve", "abandon"]
    initial_history = len(detail.json()["data"]["query_history"])
    payload = {"expected_revision": 3}
    first = await f.http.post(
        path + "/queries", json=payload, headers={"Idempotency-Key": "query-once"}
    )
    assert first.status_code == 202, first.text
    assert first.json()["code"] == 202
    second = await f.http.post(
        path + "/queries", json=payload, headers={"Idempotency-Key": "query-once"}
    )
    assert second.json()["data"]["request_id"] == first.json()["data"]["request_id"]
    status = await f.http.get(path + "/requests/" + first.json()["data"]["request_id"])
    assert status.status_code == 200, status.text
    assert status.json()["data"]["status"] == "pending"
    conflict = await f.http.post(
        path + "/queries", json={"expected_revision": 2}, headers={"Idempotency-Key": "query-once"}
    )
    assert conflict.status_code == 409
    invalid = await f.http.post(
        path + "/resolutions",
        json={
            "expected_revision": 3,
            "decision": "applied",
            "reason": "Confirmed without proof",
            "evidence_refs": [],
        },
        headers={"Idempotency-Key": "missing-evidence"},
    )
    assert invalid.status_code == 422
    abandoned = await f.http.post(
        path + "/abandon",
        json={"expected_revision": 3, "reason": "Operator stops waiting"},
        headers={"Idempotency-Key": "abandon-once"},
    )
    assert abandoned.status_code == 202, abandoned.text
    after = (await f.http.get(path)).json()["data"]
    assert after["state"] == "unknown"  # HTTP admission never pretends to settle it.
    assert len(after["query_history"]) == initial_history + 2


async def test_real_api_role_denies_foreign_scope_auditor_writes_and_revoked_actor(effect_http):
    f = effect_http
    path = f"{f.base}/{f.spec.operation_id}"
    f.holder["scope"] = OwnerScope.personal("foreign-user")
    assert (await f.http.get(path)).status_code == 403

    f.holder["scope"] = f.scope
    async with f.sessions() as session:
        await configure_session_authorization(session, f.auth)
        await session.execute(
            text("UPDATE users SET global_role='auditor' WHERE id=:id"), {"id": f.principal.user_id}
        )
        await session.commit()
    f.principal.global_role = GlobalRole.AUDITOR
    detail = await f.http.get(path)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["allowed_actions"] == []
    assert (
        await f.http.post(
            path + "/queries",
            json={"expected_revision": 3},
            headers={"Idempotency-Key": "auditor-write"},
        )
    ).status_code == 403
    async with f.sessions() as session:
        await configure_session_authorization(session, f.auth)
        await session.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": f.principal.user_id},
        )
        await session.commit()
    assert (await f.http.get(path)).status_code == 403


@pytest.mark.parametrize("effect_http", ["team"], indirect=True)
async def test_real_api_role_revalidates_team_membership_and_role(effect_http):
    f = effect_http
    path = f"{f.base}/{f.spec.operation_id}"
    for role in (TeamRole.OWNER, TeamRole.ADMIN, TeamRole.MEMBER):
        async with f.sessions() as session:
            await configure_session_authorization(session, f.auth)
            await session.execute(
                text("UPDATE team_members SET role=:role WHERE team_id=:team AND user_id=:user"),
                {"role": role.value, "team": f.scope.team_id, "user": f.principal.user_id},
            )
            await session.commit()
        f.principal.team_roles[f.scope.team_id] = role
        detail = await f.http.get(path)
        assert detail.status_code == 200, detail.text
        assert bool(detail.json()["data"]["allowed_actions"]) == (role != TeamRole.MEMBER)
        response = await f.http.post(
            path + "/queries",
            json={"expected_revision": 3},
            headers={"Idempotency-Key": "team-" + role.value},
        )
        assert response.status_code == (403 if role == TeamRole.MEMBER else 202), response.text
    async with f.sessions() as session:
        await configure_session_authorization(session, f.auth)
        await session.execute(
            text("DELETE FROM team_members WHERE team_id=:team AND user_id=:user"),
            {"team": f.scope.team_id, "user": f.principal.user_id},
        )
        await session.commit()
    assert (await f.http.get(path)).status_code == 403


async def test_real_api_role_admin_requires_explicit_cross_scope_reason(effect_http):
    f = effect_http
    owner = f.principal.user_id
    admin = "effect-admin-" + uuid4().hex
    async with f.sessions() as session:
        await configure_session_authorization(session, f.auth)
        await session.execute(
            text("INSERT INTO users(id,email,username,global_role) VALUES(:id,:email,:id,'admin')"),
            {"id": admin, "email": admin + "@test.invalid"},
        )
        await session.commit()
    f.principal.user_id = admin
    f.principal.global_role = GlobalRole.ADMIN
    f.holder["scope"] = OwnerScope.personal(admin)
    path = f"{f.base}/{f.spec.operation_id}"
    assert (await f.http.get(path)).status_code == 404
    assert (await f.http.get(path, headers={"X-Effect-Scope-User-Id": owner})).status_code == 400
    headers = {"X-Effect-Scope-User-Id": owner, "X-Effect-Scope-Reason": "Incident response proof"}
    assert (await f.http.get(path, headers=headers)).status_code == 200
    response = await f.http.post(
        path + "/queries",
        json={"expected_revision": 3},
        headers={**headers, "Idempotency-Key": "admin-cross-scope"},
    )
    assert response.status_code == 202, response.text
    request_id = response.json()["data"]["request_id"]
    async with f.sessions() as session:
        await configure_session_authorization(session, f.auth)
        row = (
            (
                await session.execute(
                    text(
                        "SELECT scope_reason,principal->>'user_id' AS actor FROM execution_effect_requests WHERE request_id=:id"
                    ),
                    {"id": request_id},
                )
            )
            .mappings()
            .one()
        )
        assert row == {"scope_reason": "Incident response proof", "actor": admin}


async def test_real_api_role_lock_serializes_revocation_and_restores_user_context(
    effect_http, monkeypatch
):
    from app.infrastructure.execution import postgres_effect_store as module

    f = effect_http
    original = module.authorize_actor
    accepted, release, revoker_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    revoker_pid = []

    async def pause_after_authority(session, *args, **kwargs):
        await original(session, *args, **kwargs)
        if not kwargs.get("write", True):
            return
        assert await session.scalar(text("SELECT current_setting('app.auth_mode')")) == "user"
        assert (
            await session.scalar(text("SELECT current_setting('app.user_id')"))
            == f.principal.user_id
        )
        assert (
            await session.scalar(
                text("SELECT count(*) FROM users WHERE id<>:id"), {"id": f.principal.user_id}
            )
            == 0
        )
        accepted.set()
        await release.wait()

    async def revoke():
        async with f.sessions() as session:
            await configure_session_authorization(session, f.auth)
            revoker_pid.append(await session.scalar(text("SELECT pg_backend_pid()")))
            revoker_started.set()
            await session.execute(
                text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
                {"id": f.principal.user_id},
            )
            await session.commit()

    monkeypatch.setattr(module, "authorize_actor", pause_after_authority)
    request = asyncio.create_task(
        f.http.post(
            f"{f.base}/{f.spec.operation_id}/queries",
            json={"expected_revision": 3},
            headers={"Idempotency-Key": "race-with-revocation"},
        )
    )
    revocation = None
    try:
        await asyncio.wait_for(accepted.wait(), 5)
        revocation = asyncio.create_task(revoke())
        await asyncio.wait_for(revoker_started.wait(), 5)
        async with f.sessions() as session:
            await configure_session_authorization(session, f.auth)
            async with asyncio.timeout(5):
                while not await session.scalar(  # noqa: ASYNC110 - observe a real independent database transaction
                    text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)"),
                    {"pid": revoker_pid[0]},
                ):
                    await asyncio.sleep(0.01)
        assert not revocation.done()
    finally:
        release.set()
        response = await asyncio.wait_for(request, 5)
        if revocation is not None:
            await asyncio.wait_for(revocation, 5)
    # The first locked idempotency lookup completes before the revocation;
    # final admission rechecks authority in its own transaction and refuses.
    assert response.status_code == 403, response.text
    async with f.sessions() as session:
        await configure_session_authorization(session, f.auth)
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM execution_effect_requests WHERE idempotency_key='race-with-revocation'"
                )
            )
            == 0
        )
    monkeypatch.setattr(module, "authorize_actor", original)
    assert (await f.http.get(f"{f.base}/{f.spec.operation_id}")).status_code == 403
