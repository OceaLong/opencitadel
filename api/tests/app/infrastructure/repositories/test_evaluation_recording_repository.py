# ruff: noqa: F811
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,  # noqa: F401
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import (
    datasets,  # noqa: F401
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_durable_create_idempotency_claim_fencing_and_audit(datasets):
    from sqlalchemy import text

    from app.application.evaluation.recording_service import RecordingService
    from app.domain.evaluation.errors import DatasetNotFound
    from app.domain.models.scope import OwnerScope, Principal

    ds, scope, principal, _, _ = datasets

    class Views:
        async def get_view(self, *args):
            return object()

    service = RecordingService(ds.uow_factory, source=SimpleNamespace(views=Views()))
    request, run_id = str(uuid4()), uuid4()

    async def create():
        return await service.create(
            scope,
            principal,
            run_id,
            [{"tool": "test", "allowed_fields": ["success"]}],
            request_id=request,
        )

    first, second = await asyncio.gather(create(), create())
    assert first == second
    assert first.status == "queued"
    assert first.result_version is None
    async with ds.uow_factory(service.auth(scope, principal)) as uow:
        token = await uow.evaluation_recording.claim(scope, first.id)
        assert token is not None
        await uow.commit()
    async with ds.uow_factory(service.auth(scope, principal)) as uow:
        assert await uow.evaluation_recording.claim(scope, first.id) is None
        await uow.evaluation_recording.fail(scope, first.id, uuid4(), "not_owner")
        assert (await uow.evaluation_recording.job(scope, first.id))["status"] == "running"
        await uow.commit()
    from tests.app.execution_test_support import execution_admin_session

    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM audit_logs WHERE action='evaluation.recording.create' AND resource_id=:id"
                ),
                {"id": str(first.id)},
            )
            == 1
        )
    with pytest.raises((DatasetNotFound, PermissionError)):
        await service.status(OwnerScope.personal("other"), Principal(user_id="other"), first.id)


async def test_durable_ledger_concurrent_claim_recovery_and_api_binding_denial(
    configurations, isolated_database
):
    import hashlib

    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.application.evaluation.recording_authority import RecordingAuthority
    from app.application.evaluation.replay_adapter import ReplayAdapter
    from app.domain.evaluation.errors import ReplayMismatch
    from app.domain.evaluation.recording import (
        MatchRule,
        RecordedContract,
        RecordingJob,
        RecordingManifest,
        RecordingSlot,
        canonical,
        recording_key,
    )
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_dataset_repository import params
    from app.infrastructure.repositories.db_uow import DBUnitOfWork
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_kernel_database_uri

    suites, ds, scope, principal = configurations
    objects = ds.objects
    from app.application.evaluation.replay_admission import prepare_replay_binding
    from app.domain.evaluation.configuration import ConfigSelection
    from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot

    draft = await suites.create(
        scope,
        principal,
        kind="config",
        name="Replay",
        definition=ConfigSelection(model_id="e02-model", tool_names=("write_file",)).model_dump(
            mode="json"
        ),
        request_id=str(uuid4()),
    )
    config = await suites.publish(
        scope,
        principal,
        kind="config",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    pair = await suites.policies.load_active_pair()
    from app.domain.execution.family import RunFamily

    snapshot = derive_run_policy_snapshot(pair.execution, RunFamily.AGENT)
    descriptor = config.snapshot["contracts"][0]
    arguments = {"filepath": "/test", "content": "recorded test data"}
    engine, _ = isolated_database
    kernel = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=engine.url.database)
    )
    settings = load_deployment_settings()

    def uow(auth=None):
        return DBUnitOfWork(
            async_sessionmaker(kernel, expire_on_commit=False),
            secret_cipher=ApiKeyCipher("test"),
            audit_signing_key="test",
            audit_signing_key_id="test",
            database_authorization_signing_secret=settings.database_authorization_signing_secret,
            authorization_context=auth or AuthorizationContext.system("execution-kernel"),
        )

    auth = AuthorizationContext.for_principal(principal, scope=scope)
    job = RecordingJob(id=uuid4(), source_run_id=uuid4())
    contract = RecordedContract(
        name="write_file",
        pack=descriptor["pack"],
        schema_body=descriptor["schema"],
        policy=descriptor["policy"],
        binding_revision="1",
        authority_revision="1",
    )
    data, object_id, version_id, run_id = canonical({"success": True}), uuid4(), uuid4(), uuid4()
    key = "test/" + str(object_id)
    await objects.put_bytes(key, data)
    slot = RecordingSlot(
        id=uuid4(),
        tool="write_file",
        contract_digest=contract.digest,
        match_key=recording_key("write_file", contract.digest, arguments, "root", 0),
        rule=MatchRule(),
        branch="root",
        ordinal=0,
        object_id=object_id,
        result_digest=hashlib.sha256(data).hexdigest(),
        result_bytes=len(data),
        simulated_effect=True,
    )
    manifest = RecordingManifest(
        id=version_id,
        job_id=job.id,
        source_run_id=job.source_run_id,
        catalog_fingerprint="fixed",
        contracts=(contract,),
        slots=(slot,),
        pins=(),
    )
    try:
        async with uow(auth) as work:
            repo = work.evaluation_recording
            await repo.create(scope, job, [], principal)
            token = await repo.claim(scope, job.id)
            await work.db_session.execute(
                text(
                    "INSERT INTO evaluation_recording_objects(id,job_id,storage_key,digest,size_bytes,owner_user_id,team_id,created_by) VALUES(:id,:job,:key,:digest,:size,:owner,:team,:actor)"
                ),
                params(
                    scope,
                    id=object_id,
                    job=job.id,
                    key=key,
                    digest=slot.result_digest,
                    size=len(data),
                ),
            )
            await repo.publish(scope, manifest, token)
            await prepare_replay_binding(
                work,
                suites,
                scope,
                principal,
                run_id=run_id,
                source_entity_id="case-one",
                config_version_id=config.id,
                recording_version_id=version_id,
                policy_pair=pair,
                policy_snapshot=snapshot,
            )
            await work.commit()
        async with ds.uow_factory(auth) as work:
            with pytest.raises(DBAPIError):
                await work.evaluation_recording.bind(scope, uuid4(), version_id, principal)
        adapter = ReplayAdapter(RecordingAuthority(uow), objects)
        context = SimpleNamespace(
            activity_id=uuid4(),
            run=SimpleNamespace(
                run_id=run_id,
                owner_scope=scope,
                source_entity_type="evaluation_recorded_case",
                source_entity_id="case-one",
                policy_snapshot=snapshot,
            ),
        )

        async def match():
            return await adapter.match(context, "write_file", contract.digest, arguments, "root", 0)

        original_get = objects.get_bytes
        reads = []

        async def counted_get(key):
            reads.append(key)
            return await original_get(key)

        objects.get_bytes = counted_get
        with pytest.raises(ReplayMismatch, match="current_approval_required"):
            await match()
        assert reads == []
        from tests.app.execution_test_support import execution_admin_session

        async with execution_admin_session() as db:
            await db.execute(
                text(
                    """INSERT INTO execution_approval_projection(approval_id,run_id,source_entity_type,source_entity_id,approval_kind,subject_activity_id,subject_label,risk_summary,status,owner_user_id,request_event_position,requested_at) VALUES(:id,:run,'evaluation_recorded_case','case-one','tool_effect',:activity,'write_file','simulated write','approved',:owner,1,CURRENT_TIMESTAMP)"""
                ),
                {
                    "id": uuid4(),
                    "run": run_id,
                    "activity": context.activity_id,
                    "owner": scope.user_id,
                },
            )
            await db.commit()
        first, second = await asyncio.gather(match(), match())
        assert first.simulated_effect

        assert first == second
        assert await match() == first  # crash after durable consume, before handler settlement
        another = SimpleNamespace(activity_id=uuid4(), run=context.run)
        async with execution_admin_session() as db:
            await db.execute(
                text(
                    """INSERT INTO execution_approval_projection(approval_id,run_id,source_entity_type,source_entity_id,approval_kind,subject_activity_id,subject_label,risk_summary,status,owner_user_id,request_event_position,requested_at) VALUES(:id,:run,'evaluation_recorded_case','case-one','tool_effect',:activity,'write_file','simulated write','approved',:owner,2,CURRENT_TIMESTAMP)"""
                ),
                {
                    "id": uuid4(),
                    "run": run_id,
                    "activity": another.activity_id,
                    "owner": scope.user_id,
                },
            )
            await db.commit()
        with pytest.raises(ReplayMismatch, match="slot_already_consumed"):
            await adapter.match(another, "write_file", contract.digest, arguments, "root", 0)
        async with uow(auth) as work:
            assert (await work.evaluation_recording.coverage(scope, run_id))["consumed"] == 1
        from tests.app.execution_test_support import execution_admin_session

        async with execution_admin_session() as db:
            await db.execute(
                text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
                {"id": principal.user_id},
            )
            await db.commit()
        with pytest.raises(ReplayMismatch, match="recording_authority_revoked"):
            await match()
    finally:
        await kernel.dispose()


async def test_real_f06_worker_catalog_source_compatibility_in_owned_database():
    from tests.app.application.services.test_execution_content_production import (
        test_real_worker_catalog_captures_full_inputs_outputs_and_owned_fixed_citation,
    )

    await test_real_worker_catalog_captures_full_inputs_outputs_and_owned_fixed_citation()


@pytest.mark.parametrize("capture_available", [True, False])
async def test_real_generation_reads_accepted_f06_pages_publishes_pins_and_reclaims(
    configurations, isolated_database, capture_available
):
    import json
    from datetime import UTC, datetime, timedelta
    from uuid import NAMESPACE_URL, uuid5

    from app.application.evaluation.recording_service import RecordingService
    from app.application.evaluation.recording_source import RecordingSource
    from app.application.evaluation.recording_worker import RecordingWorker
    from app.application.execution.activity_inputs import ActivityObjectStore
    from app.application.execution.activity_registry import ActivityRegistry
    from app.application.execution.activity_worker import ActivityWorker
    from app.application.execution.run_service import RunService
    from app.application.services.execution_content_service import ExecutionContentService
    from app.application.services.execution_view_service import ExecutionViewService
    from app.domain.evaluation.recording import RecordedContract
    from app.domain.execution.activity import ActivityOutcome
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.execution.run import RunAggregate
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
    from app.infrastructure.execution.postgres_execution_content import ExecutionContentWriter
    from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from app.infrastructure.execution.postgres_run_context_source import PostgresRunContextSource
    from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
    from app.infrastructure.repositories.db_evaluation_recording_repository import (
        DBEvaluationRecordingRepository,
    )
    from app.infrastructure.repositories.recording_object_lifecycle import RecordingObjectLifecycle
    from tests.app.execution_test_support import execution_admin_session, run_policy_snapshot_json

    suites, ds, scope, principal = configurations
    objects, old_lifecycle = ds.objects, ds.object_intents
    run_id = uuid4()
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    kernel_auth = AuthorizationContext.system("e03-source-test")
    orchestrator = SqlAlchemyExecutionOrchestrator(
        session_factory=execution_admin_session,
        aggregates={"run": RunAggregate()},
        authorization=kernel_auth,
    )

    async def command(name, payload, schema=1):
        result = await orchestrator.handle(
            CommandEnvelope(
                command_id=uuid4(),
                command_type=name,
                command_schema_version=schema,
                stream_type="run",
                stream_id=str(run_id),
                owner_user_id=scope.user_id,
                team_id=None,
                correlation_id=run_id,
                causation_id=None,
                issued_at=datetime.now(UTC),
                payload=payload,
            )
        )
        assert result.status == "accepted", result

    await command(
        "CreateRun",
        {
            "family": "agent",
            "source_entity_type": "session",
            "source_entity_id": "recording-source",
            "semantic_payload": {},
            "policy_snapshot": run_policy_snapshot_json("agent"),
        },
    )
    await command("StartRun", {})
    storage = ActivityObjectStore(objects)
    input_ref, input_digest = await storage.put_input(
        run_id, {"message": "query", "mode": "agent", "session_id": "recording-source"}
    )
    from app.application.evaluation.configuration_metadata import builtin_contracts

    descriptor = builtin_contracts(("read_file",), mode="agent", allowed_tools=None)[0]
    contract = RecordedContract(
        name="read_file",
        pack=descriptor["pack"],
        schema_body=descriptor["schema"],
        policy=descriptor["policy"],
        binding_revision="one",
        authority_revision="one",
    )
    unused_remote = RecordedContract(
        name="unused_remote_agent",
        pack="a2a",
        schema_body={
            "type": "function",
            "function": {"name": "unused_remote_agent", "parameters": {"type": "object"}},
        },
        policy=descriptor["policy"],
        connector_id="unused-connector",
        binding_revision="placeholder",
        authority_revision="placeholder",
    )
    content = "complete output " * 7000
    steps = [
        (
            "retrieval:0",
            "retrieval.search",
            {},
            {
                "kind": "retrieval",
                "message": {
                    "role": "system",
                    "content": json.dumps({"query": "query", "sources": []}),
                },
            },
            None,
        ),
        (
            "model:0",
            "model.call",
            {"round": 0},
            {
                "kind": "model",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-one",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"filepath":"/test"}'},
                        }
                    ],
                },
            },
            None,
        ),
        (
            "tool:0:0:call-one",
            "tool.call",
            {
                "round": 0,
                "tool_call": {
                    "call_id": "call-one",
                    "name": "read_file",
                    "arguments": {"filepath": "/test"},
                },
            },
            {
                "kind": "tool",
                "message": {
                    "role": "tool",
                    "tool_call_id": "call-one",
                    "name": "read_file",
                    "content": json.dumps({"success": True, "message": content}),
                },
            },
            "model:0",
        ),
    ]

    def identity(key):
        return uuid5(NAMESPACE_URL, f"opencitadel:{run_id}:activity:0:{key}")

    projector = PostgresFormalProjector(
        session_factory=execution_admin_session, authorization=kernel_auth
    )
    for key, kind, payload, output, parent in steps:
        await command(
            "RequestActivity",
            {
                "activity_id": str(identity(key)),
                "activity_type": kind,
                "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                "input_ref": input_ref,
                "input_digest": input_digest,
                "input_payload": payload,
                "parent_activity_id": str(identity(parent)) if parent else None,
            },
            2,
        )
        await projector.run_once(scope, limit=1000)

        class Handler:
            activity_type = kind
            idempotent = True

            async def execute(self, request, context, kind=kind, output=output):
                if kind == "model.call":
                    async with execution_admin_session() as db:
                        await DBEvaluationRecordingRepository(db).capture(
                            scope,
                            run_id,
                            request.activity_id,
                            {
                                "fingerprint": "fixed",
                                "contracts": [
                                    contract.model_dump(mode="json"),
                                    unused_remote.model_dump(mode="json"),
                                ],
                            }
                            if capture_available
                            else {"unavailable": "capture_schema_redacted"},
                        )
                        await db.commit()
                ref = await storage.put_result(request.activity_id, output)
                return ActivityOutcome.succeeded(result_ref=ref, result_summary="truncated")

        registry = ActivityRegistry()
        registry.register(Handler())
        worker = ActivityWorker(
            store=PostgresActivityStore(
                session_factory=execution_admin_session, authorization=kernel_auth
            ),
            run_contexts=PostgresRunContextSource(
                session_factory=execution_admin_session, authorization=kernel_auth
            ),
            run_service=RunService(orchestrator=orchestrator),
            registry=registry,
            worker_id="e03-source",
            content_writer=ExecutionContentWriter(
                session_factory=execution_admin_session, authorization=kernel_auth, objects=storage
            ),
        )
        stats = await worker.run_once(now=datetime.now(UTC), limit=1)
        assert stats.succeeded == 1, stats
        await projector.run_once(scope, limit=1000)
    await command("CompleteRun", {})
    await projector.run_once(scope, limit=1000)
    views = ExecutionViewService(
        PostgresExecutionView(session_factory=ds.uow_factory().session_factory, authorization=auth),
        cursor_secret=b"e03-full-source-secret",
    )
    reader = ExecutionContentService(
        lambda: ds.uow_factory(auth), views, None, cursor_secret=b"e03-content-source-secret"
    )
    service = RecordingService(
        ds.uow_factory,
        source=RecordingSource(reader, views),
        cursor_secret=b"e10-recording-discovery-secret",
    )
    if capture_available:
        candidates = await service.candidates(scope, principal, run_id, limit=1)
        assert len(candidates.items) == 1
        assert candidates.next_cursor
        rest = await service.candidates(
            scope, principal, run_id, cursor=candidates.next_cursor, limit=1
        )
        assert rest.at == candidates.at
        assert {c.tool for c in (*candidates.items, *rest.items)} == {"read_file", "__retrieval__"}
        assert "source_content" not in candidates.model_dump_json()
        assert content not in candidates.model_dump_json()
        with pytest.raises(ValueError, match="cursor"):
            await service.candidates(scope, principal, run_id, cursor="tampered")

    selections = [
        {"tool": "read_file", "allowed_fields": ["success", "message"]},
        {"tool": "__retrieval__", "allowed_fields": ["query", "sources"]},
    ]
    job = await service.create(scope, principal, run_id, selections, request_id=str(uuid4()))
    assert job.status == "queued"
    lifecycle = RecordingObjectLifecycle(
        old_lifecycle.factory, objects, signing_secret=old_lifecycle.secret
    )
    generator = RecordingWorker(service, lifecycle)
    await generator.generate(scope, principal, job.id)
    status = await service.status(scope, principal, job.id)
    if not capture_available:
        assert status.status == "failed"
        assert status.error == "contract_unavailable"
        assert status.result_version is None
        return
    assert status.status == "ready", status
    async with ds.uow_factory(auth) as work:
        manifest = await work.evaluation_recording.version(scope, status.result_version)
        assert {item.name for item in manifest.contracts} == {"read_file", "__retrieval__"}
        assert manifest.catalog_fingerprint == "fixed"
    discovery = await service.list_jobs(scope, principal, limit=1)
    assert discovery.items[0].id == job.id
    import httpx
    from fastapi import FastAPI

    from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.evaluation_recording_routes import router
    from app.interfaces.errors.exception_handlers import register_exception_handlers
    from app.interfaces.service_dependencies import get_recording_service

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_recording_service] = lambda: service
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        scope=scope, principal=principal
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get(f"/evaluation/recordings/sources/{run_id}?limit=1")
        assert response.status_code == 200, response.text
        assert content not in response.text
        next_cursor = response.json()["data"]["next_cursor"]
        continued = await client.get(
            f"/evaluation/recordings/sources/{run_id}", params={"cursor": next_cursor, "limit": 1}
        )
        assert continued.status_code == 200
        assert (await client.get("/evaluation/recordings")).json()["data"]["items"][0]["id"] == str(
            job.id
        )
        from tests.app.application.services.test_artifact_provenance_postgres import seed

        other, _ = await seed()
        app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
            scope=OwnerScope.personal(other), principal=Principal(user_id=other)
        )
        assert (await client.get("/evaluation/recordings")).json()["data"]["items"] == []
        denied = await client.get(f"/evaluation/recordings/sources/{run_id}")
        assert denied.status_code in {403, 404}, denied.text
        wrong_cursor = await client.get(
            f"/evaluation/recordings/sources/{run_id}", params={"cursor": next_cursor}
        )
        assert wrong_cursor.status_code == 400

    await generator.generate(scope, principal, job.id)
    async with ds.uow_factory(auth) as work:
        manifest = await work.evaluation_recording.version(scope, status.result_version)
        assert len(manifest.pins) == 6
        assert len(manifest.slots) == 2
        recorded = next(s for s in manifest.slots if s.tool == "read_file")
        obj = await work.evaluation_recording.object(scope, recorded.object_id)
        assert json.loads(await objects.get_bytes(obj["storage_key"]))["message"] == content
        assert all(
            p.available
            for p in await work.resource_pins.validate(
                scope, "recording_version", str(manifest.id), manifest.pins
            )
        )

    # Replay a new durable Run using this generated manifest. Every external path is forbidden.
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.application.evaluation.recording_authority import RecordingAuthority
    from app.application.evaluation.replay_admission import prepare_replay_binding
    from app.application.evaluation.replay_runtime import ReplayRuntime
    from app.application.execution.activities.retrieval import RetrievalActivityHandler
    from app.application.execution.activities.tool_call import ToolCallActivityHandler
    from app.application.execution.agent_tool_catalog import AgentToolCatalog
    from app.domain.evaluation.configuration import ConfigSelection
    from app.domain.execution.family import RunFamily
    from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot
    from app.infrastructure.repositories.db_uow import DBUnitOfWork
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_kernel_database_uri

    draft = await suites.create(
        scope,
        principal,
        kind="config",
        name="Generated replay",
        definition=ConfigSelection(model_id="e02-model", tool_names=("read_file",)).model_dump(
            mode="json"
        ),
        request_id=str(uuid4()),
    )
    config = await suites.publish(
        scope,
        principal,
        kind="config",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    pair = await suites.policies.load_active_pair()
    snapshot = derive_run_policy_snapshot(pair.execution, RunFamily.AGENT)
    engine, _ = isolated_database
    kernel = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=engine.url.database)
    )
    settings = load_deployment_settings()

    def kernel_uow(authorization=None):
        return DBUnitOfWork(
            async_sessionmaker(kernel, expire_on_commit=False),
            secret_cipher=ApiKeyCipher("test"),
            audit_signing_key="test",
            audit_signing_key_id="test",
            database_authorization_signing_secret=settings.database_authorization_signing_secret,
            authorization_context=authorization or kernel_auth,
        )

    def source_factory(authorization):
        replay_views = ExecutionViewService(
            PostgresExecutionView(
                session_factory=ds.uow_factory().session_factory, authorization=authorization
            ),
            cursor_secret=b"e03-full-source-secret",
        )
        return RecordingSource(
            ExecutionContentService(
                lambda: ds.uow_factory(authorization),
                replay_views,
                None,
                cursor_secret=b"e03-content-source-secret",
            ),
            replay_views,
        )

    runtime = ReplayRuntime(RecordingAuthority(kernel_uow), objects, source_factory)

    class Forbidden:
        async def invoke(self, *args, **kwargs):
            pytest.fail("real tool invoked")

        async def retrieve(self, *args, **kwargs):
            pytest.fail("real retrieval invoked")

        async def recall_for_session(self, *args, **kwargs):
            pytest.fail("real memory invoked")

    catalog = object.__new__(AgentToolCatalog)
    catalog._replay = runtime

    async def forbidden_build(*args, **kwargs):
        pytest.fail("real catalog or MCP/A2A initialization")

    catalog._build = forbidden_build
    try:
        run_id = uuid4()  # closure identities and commands now refer to replay Run
        async with kernel_uow(auth) as work:
            await prepare_replay_binding(
                work,
                suites,
                scope,
                principal,
                run_id=run_id,
                source_entity_id="generated-case",
                config_version_id=config.id,
                recording_version_id=manifest.id,
                policy_pair=pair,
                policy_snapshot=snapshot,
            )
            await work.commit()
        await command(
            "CreateRun",
            {
                "family": "agent",
                "source_entity_type": "evaluation_recorded_case",
                "source_entity_id": "generated-case",
                "semantic_payload": {},
                "policy_snapshot": snapshot.model_dump(mode="json"),
            },
        )
        await command("StartRun", {})
        for key, kind, payload, output, parent in steps:
            await command(
                "RequestActivity",
                {
                    "activity_id": str(identity(key)),
                    "activity_type": kind,
                    "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                    "input_ref": input_ref,
                    "input_digest": input_digest,
                    "input_payload": payload,
                    "parent_activity_id": str(identity(parent)) if parent else None,
                },
                2,
            )
            await projector.run_once(scope, limit=1000)
            registry = ActivityRegistry()
            if kind == "retrieval.search":
                handler = RetrievalActivityHandler(
                    objects=storage, tools=Forbidden(), memories=Forbidden(), replay=runtime
                )
            elif kind == "tool.call":
                handler = ToolCallActivityHandler(
                    objects=storage, tools=Forbidden(), replay=runtime
                )
            else:

                class Model:
                    activity_type = "model.call"
                    idempotent = True

                    async def execute(self, request, context, output=output):
                        definitions = await catalog.definitions({}, context)
                        assert [d.name for d in definitions.definitions] == ["read_file"]
                        return ActivityOutcome.succeeded(
                            result_ref=await storage.put_result(request.activity_id, output)
                        )

                handler = Model()
            registry.register(handler)
            worker = ActivityWorker(
                store=PostgresActivityStore(
                    session_factory=execution_admin_session, authorization=kernel_auth
                ),
                run_contexts=PostgresRunContextSource(
                    session_factory=execution_admin_session, authorization=kernel_auth
                ),
                run_service=RunService(orchestrator=orchestrator),
                registry=registry,
                worker_id="e03-replay",
                content_writer=ExecutionContentWriter(
                    session_factory=execution_admin_session,
                    authorization=kernel_auth,
                    objects=storage,
                ),
            )
            stats = await worker.run_once(now=datetime.now(UTC), limit=1)
            assert stats.succeeded == 1, stats
            await projector.run_once(scope, limit=1000)
        async with kernel_uow(auth) as work:
            assert (await work.evaluation_recording.coverage(scope, run_id))["consumed"] == 2
        assert (
            await RecordingObjectLifecycle(
                async_sessionmaker(kernel, expire_on_commit=False),
                objects,
                signing_secret=settings.database_authorization_signing_secret,
            ).cleanup(now=datetime.now(UTC) + timedelta(hours=2))
            == 0
        )
    finally:
        await kernel.dispose()


async def test_object_intent_crash_reclaim_fence_and_cleanup(datasets, isolated_database):
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.domain.evaluation.errors import DatasetConflict
    from app.domain.evaluation.recording import RecordingJob
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.recording_object_lifecycle import RecordingObjectLifecycle
    from tests.app.execution_test_support import (
        execution_admin_session,
        execution_kernel_database_uri,
    )

    ds, scope, principal, objects, original = datasets
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    job = RecordingJob(id=uuid4(), source_run_id=uuid4())
    async with ds.uow_factory(auth) as work:
        await work.evaluation_recording.create(scope, job, [], principal)
        first = await work.evaluation_recording.claim(scope, job.id)
        await work.commit()

    class CrashingObjects:
        async def put_bytes(self, key, body):
            await objects.put_bytes(key, body)
            raise OSError("simulated writer process failure")

    lifecycle = RecordingObjectLifecycle(
        original.factory, CrashingObjects(), signing_secret=original.secret
    )
    with pytest.raises(OSError, match="simulated writer process failure"):
        await lifecycle.put(auth, job_id=job.id, claim_token=first, body=b"crashed result")
    async with execution_admin_session() as db:
        row = (
            await db.execute(
                text("SELECT id,storage_key FROM evaluation_recording_objects WHERE job_id=:job"),
                {"job": job.id},
            )
        ).one()
        await db.execute(
            text(
                "UPDATE evaluation_recording_objects SET created_at=CURRENT_TIMESTAMP-INTERVAL '2 hours' WHERE id=:id"
            ),
            {"id": row.id},
        )
        await db.execute(
            text(
                "UPDATE evaluation_recording_jobs SET lease_until=CURRENT_TIMESTAMP-INTERVAL '1 minute' WHERE id=:job"
            ),
            {"job": job.id},
        )
        await db.commit()
    lifecycle = RecordingObjectLifecycle(original.factory, objects, signing_secret=original.secret)
    with pytest.raises(DatasetConflict, match="claim_lost"):
        await lifecycle.put(auth, job_id=job.id, claim_token=first, body=b"stale writer")
    async with ds.uow_factory(auth) as work:
        second = await work.evaluation_recording.claim(scope, job.id)
        assert second is not None
        assert second != first
        await work.evaluation_recording.fail(scope, job.id, first, "stale")
        assert (await work.evaluation_recording.job(scope, job.id))["status"] == "running"
        await work.commit()
    engine, _ = isolated_database
    kernel = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=engine.url.database)
    )
    cleaner = RecordingObjectLifecycle(
        async_sessionmaker(kernel, expire_on_commit=False), objects, signing_secret=original.secret
    )
    try:
        assert await cleaner.cleanup() == 0
        assert await objects.get_bytes(row.storage_key) == b"crashed result"
        async with ds.uow_factory(auth) as work:
            await work.evaluation_recording.fail(scope, job.id, second, "test_failure")
            await work.commit()
        async with execution_admin_session() as lock:
            await lock.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": "recording-object:" + str(row.id)},
            )
            assert await cleaner.cleanup() == 0
            await lock.rollback()
        assert await cleaner.cleanup() == 1
        assert await cleaner.cleanup() == 0
        with pytest.raises((FileNotFoundError, KeyError)):
            await objects.get_bytes(row.storage_key)
    finally:
        await kernel.dispose()


async def test_recording_external_authority_borrows_current_uow_without_discovery(
    datasets, monkeypatch
):
    from sqlalchemy import text

    from app.application.evaluation.recording_authority import (
        RecordingExternalContracts,
        RecordingPreflightAuthority,
    )
    from app.domain.evaluation.configuration import ConfigSelection, ExternalContractReference
    from app.domain.evaluation.errors import ReplayMismatch
    from app.domain.evaluation.recording import RecordedContract, RecordingJob, RecordingManifest
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.services.tools.capability_policy import READ_SAFE
    from app.domain.services.tools.mcp import MCPTool
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher
    from tests.app.execution_test_support import execution_admin_session

    ds, scope, principal, objects, _ = datasets
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    connector = "e03-connector"
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "INSERT INTO mcp_servers(id,name,url,owner_user_id,visibility) VALUES(:id,'source','http://invalid.test/mcp',:owner,'private')"
            ),
            {"id": connector, "owner": scope.user_id},
        )
        await db.commit()
    job = RecordingJob(id=uuid4(), source_run_id=uuid4())
    async with ds.uow_factory(auth) as work:
        revision = await work.evaluation_recording.connector_binding(scope, "mcp", connector)
        contract = RecordedContract(
            name="mcp_explicit_source",
            pack="mcp",
            schema_body={
                "type": "function",
                "function": {"name": "mcp_explicit_source", "parameters": {"type": "object"}},
            },
            policy=READ_SAFE,
            connector_id=connector,
            source_name="remote_actual_name",
            connector_bindings={connector: revision},
            binding_revision=revision,
            authority_revision=revision,
        )
        manifest = RecordingManifest(
            id=uuid4(),
            job_id=job.id,
            source_run_id=job.source_run_id,
            catalog_fingerprint="fixed",
            contracts=(contract,),
            slots=(),
            pins=(),
        )
        await work.evaluation_recording.create(scope, job, [], principal)
        token = await work.evaluation_recording.claim(scope, job.id)
        await work.evaluation_recording.publish(scope, manifest, token)
        await work.commit()

    def forbidden(*args, **kwargs):
        pytest.fail("metadata preflight performed runtime discovery, decryption or body read")

    monkeypatch.setattr(objects, "get_bytes", forbidden)
    monkeypatch.setattr(ApiKeyCipher, "decrypt_versioned", forbidden)
    monkeypatch.setattr(MCPTool, "initialize", forbidden)
    reference = ExternalContractReference(kind="recording", version_id=manifest.id)
    config = SimpleNamespace(
        selection=ConfigSelection(model_id="metadata-only", external_contract_ref=reference)
    )
    suite = SimpleNamespace(recording_versions=(manifest.id,))
    async with ds.uow_factory(auth) as work:
        selected = await RecordingExternalContracts(work).contracts(
            scope, principal, (contract.name,), reference=reference
        )
        assert selected[0].schema_body == contract.schema_body
        evidence = await RecordingPreflightAuthority().check_in_uow(
            scope, suite, [config], for_start=True, uow=work, principal=principal
        )
        assert evidence.ready
        builtin_only = SimpleNamespace(selection=ConfigSelection(model_id="metadata-only"))
        assert (
            await RecordingPreflightAuthority().check_in_uow(
                scope, suite, [builtin_only], for_start=False, uow=work, principal=principal
            )
        ).ready
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE mcp_servers SET url='http://changed.invalid/mcp' WHERE id=:id"),
            {"id": connector},
        )
        await db.commit()
    async with ds.uow_factory(auth) as work:
        with pytest.raises(ReplayMismatch, match="connector_changed"):
            await RecordingExternalContracts(work).contracts(
                scope, principal, (contract.name,), reference=reference
            )
        evidence = await RecordingPreflightAuthority().check_in_uow(
            scope, suite, [config], for_start=True, uow=work, principal=principal
        )
        assert not evidence.ready
        assert "recording_unavailable" in evidence.errors


async def test_revoked_inventory_page_terminates_without_bodies_and_valid_job_progresses(
    datasets, isolated_database, monkeypatch
):
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.application.evaluation.recording_worker import RecordingWorker
    from app.domain.evaluation.recording import RecordingJob
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_uow import DBUnitOfWork
    from app.infrastructure.repositories.recording_maintenance import RecordingMaintenance
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import (
        execution_admin_session,
        execution_kernel_database_uri,
    )

    ds, scope, principal, objects, _ = datasets
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    revoked = [RecordingJob(id=uuid4(), source_run_id=uuid4()) for _ in range(5)]
    async with ds.uow_factory(auth) as work:
        for job in revoked:
            await work.evaluation_recording.create(scope, job, [], principal)
        stale = await work.evaluation_recording.job(scope, revoked[0].id)
        await work.commit()
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": principal.user_id},
        )
        await db.commit()
    current = principal.model_copy(update={"token_version": principal.token_version + 1})
    current_auth = AuthorizationContext.for_principal(current, scope=scope)
    valid = RecordingJob(id=uuid4(), source_run_id=uuid4())
    async with ds.uow_factory(current_auth) as work:
        await work.evaluation_recording.create(scope, valid, [], current)
        await work.commit()
        with pytest.raises(PermissionError, match="kernel_only"):
            await work.evaluation_recording.fail_revoked_inventory(scope, stale)
    engine, _ = isolated_database
    kernel = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=engine.url.database)
    )
    settings = load_deployment_settings()

    def uow():
        return DBUnitOfWork(
            async_sessionmaker(kernel, expire_on_commit=False),
            secret_cipher=ApiKeyCipher("test"),
            audit_signing_key="test",
            audit_signing_key_id="test",
            database_authorization_signing_secret=settings.database_authorization_signing_secret,
            authorization_context=AuthorizationContext.system("recording-maintenance"),
        )

    visited = []

    async def generate(self, selected_scope, selected_principal, job_id):
        assert selected_principal == current
        visited.append(job_id)

    monkeypatch.setattr(RecordingWorker, "generate", generate)

    def forbidden(*args):
        pytest.fail("revoked maintenance read private result")

    monkeypatch.setattr(objects, "get_bytes", forbidden)
    maintenance = RecordingMaintenance(
        uow, lambda auth: SimpleNamespace(uow_factory=ds.uow_factory), None
    )
    try:
        assert await maintenance.process_pending() == 5
        assert visited == []
        assert await maintenance.process_pending() == 1
        assert visited == [valid.id]
        async with uow() as work:
            assert not await work.evaluation_recording.fail_revoked_inventory(scope, stale)
            row = await work.evaluation_recording.job(scope, valid.id)
            await work.evaluation_recording.claim(scope, valid.id)
            assert not await work.evaluation_recording.fail_revoked_inventory(scope, row)
            await work.commit()
        async with execution_admin_session() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT actor_user_id,metadata FROM audit_logs WHERE action='evaluation.recording.revoked'"
                    )
                )
            ).all()
            assert len(rows) == 5
            assert all(
                row.actor_user_id is None and row.metadata["requester_user_id"] == principal.user_id
                for row in rows
            )
    finally:
        await kernel.dispose()
