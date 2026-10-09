"""Fresh isolated DB, real production API factories and persisted recovery."""

from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.composition.api import open_api_runtime
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import Principal
from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
from app.interfaces.endpoints.routes import create_api_routes
from app.interfaces.errors.exception_handlers import register_exception_handlers
from core.config import load_deployment_settings
from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_artifact_provenance_postgres import (
    kernel_session,
    production_run,
    seed,
)
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)
from tests.app.artifact_test_support import UnitOfWorkUploadIntents
from tests.app.composition.test_api_runtime import _PolicyRepository, _resource_factories
from tests.app.execution_test_support import execution_admin_session

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_production_api_factories_read_scoped_persistent_events_and_exact_view(
    isolated_database,  # noqa: F811
):
    admin, _ = isolated_database
    _, producer, command, _ = await production_run()
    projector = PostgresFormalProjector(
        session_factory=kernel_session,
        authorization=AuthorizationContext.system("f08-test"),
    )
    await projector.run_once(producer.scope, limit=100)
    settings = load_deployment_settings()
    engine = create_async_engine(
        make_url(settings.sqlalchemy_database_uri).set(
            drivername="postgresql+asyncpg", database=admin.url.database
        )
    )
    sessions = async_sessionmaker(
        engine,
        info={
            "database_authorization_signing_secret": settings.database_authorization_signing_secret
        },
    )

    class Postgres:
        session_factory = sessions
        upload_intent_session_factory = sessions

        async def init(self):
            pass

        async def shutdown(self):
            pass

    factories = replace(_resource_factories([]), postgres=lambda _: Postgres())
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(create_api_routes(), prefix="/api")
    principal = Principal(user_id=producer.scope.user_id)

    @app.middleware("http")
    async def authenticate(request, call_next):
        from app.interfaces.auth_context import current_principal, set_principal

        token = set_principal(principal)
        try:
            return await call_next(request)
        finally:
            current_principal.reset(token)

    try:
        async with open_api_runtime(
            settings,
            factories=factories,
            runtime_policy_repository_factory=lambda _: _PolicyRepository(),
        ) as runtime:
            app.state.runtime = runtime
            assert not runtime.supervisor.pending_names
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as http:
                base = f"/api/execution-runs/{producer.run_id}"
                response = await http.get(base + "/view")
                assert response.status_code == 200, response.text
                view = response.json()["data"]
                assert view["run"]["run_id"] == str(producer.run_id)
                assert isinstance(view["at"], str)
                detail = await http.get(
                    base + "/steps/" + view["steps"][0]["step_id"], params={"at": view["at"]}
                )
                assert detail.status_code == 200, detail.text
                assert detail.json()["data"]["at"] == view["at"]
                events = await http.get(base + "/events", params={"limit": 1})
                assert events.status_code == 200, events.text
                first = events.json()["data"]
                assert len(first["events"]) == 1
                cursor = first["events"][0]["cursor"]
                after = await http.get(base + "/events", params={"after": cursor})
                assert after.status_code == 200
                assert all(event["cursor"] != cursor for event in after.json()["data"]["events"])
                wrong = await http.get(
                    f"/api/execution-runs/{uuid4()}/events", params={"after": cursor}
                )
                assert wrong.status_code == 400
                header_conflict = await http.get(
                    base + "/events/stream",
                    params={"after": cursor},
                    headers={"Last-Event-ID": "other"},
                )
                assert header_conflict.status_code == 400
                await command("FailRun", {"failure_code": "test_terminal"})
                await projector.run_once(producer.scope, limit=100)
                appended = (await http.get(base + "/events", params={"after": cursor})).json()[
                    "data"
                ]["events"]
                assert len(appended) > len(after.json()["data"]["events"])
                # Real SSE endpoint iterator and production reader, transport disconnect only supplied.
                from app.domain.models.scope import WorkspaceContext
                from app.interfaces.endpoints.execution_view_routes import stream_events

                class Connected:
                    async def is_disconnected(self):
                        return False

                ctx = WorkspaceContext(principal=principal, scope=producer.scope)
                event_service = runtime.execution_event_factory(
                    AuthorizationContext.for_principal(principal, scope=producer.scope)
                )
                stream = await stream_events(
                    Connected(),
                    producer.run_id,
                    after=None,
                    last_event_id=cursor,
                    ctx=ctx,
                    service=event_service,
                )
                emitted = []
                try:
                    for _ in appended:
                        event = await anext(stream.body_iterator)
                        emitted.append(event.id)
                finally:
                    await stream.body_iterator.aclose()
                assert emitted == [event["cursor"] for event in appended]
                await projector.rebuild(producer.scope)
                rebuilt = await http.get(base + "/events", params={"after": cursor})
                assert rebuilt.status_code == 409, rebuilt.text
                foreign_user, _ = await seed()
                principal = Principal(user_id=foreign_user)
                assert (await http.get(base + "/view")).status_code == 404
                assert (await http.get(base + "/events")).status_code == 404
    finally:
        await engine.dispose()


async def test_historical_artifact_pages_are_complete_fixed_safe_and_reauthorize():
    from sqlalchemy import text

    from app.application.services.artifact_service import ArtifactService, sanitize_html_for_preview
    from app.application.services.execution_content_service import ExecutionContentService
    from app.application.services.execution_view_service import ExecutionViewService
    from app.domain.models.scope import WorkspaceContext
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )
    from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.execution_view_routes import router
    from app.interfaces.service_dependencies import get_execution_content_service
    from tests.app.application.services.test_artifact_provenance_postgres import Objects, uow
    from tests.app.application.services.test_f06_review_fixes import readers

    session_id, producer, _command, handler = await production_run()
    objects = Objects()
    raw = '<script>bad()</script><p onclick="bad()">' + "汉🙂" * 40000 + "END</p>"
    artifact = await ArtifactService(
        uow, objects, upload_intents=UnitOfWorkUploadIntents(uow)
    ).write_content(session_id, None, "web", "safe", raw, producer=producer, verify_upload=False)
    auth = AuthorizationContext.system("f08-content-test")
    projector = PostgresFormalProjector(session_factory=kernel_session, authorization=auth)
    await projector.run_once(producer.scope, limit=1000)
    views = ExecutionViewService(
        PostgresExecutionView(session_factory=execution_admin_session, authorization=auth),
        cursor_secret=b"0123456789abcdef",
    )
    old = await views.get_view(producer.scope, producer.run_id)
    maintenance = ArtifactProvenanceMaintenance(
        session_factory=execution_admin_session,
        authorization=auth,
        objects=objects,
        handler=handler,
    )
    await maintenance.process_pending()
    await projector.run_once(producer.scope, limit=1000)
    view = await views.get_view(producer.scope, producer.run_id)
    step = next(step for step in view.steps if step.artifact_refs)
    content = ExecutionContentService(
        readers,
        views,
        ArtifactService(readers, objects, upload_intents=UnitOfWorkUploadIntents(readers)),
        cursor_secret=b"0123456789abcdef",
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api")
    ctx = WorkspaceContext(
        principal=Principal(user_id=producer.scope.user_id), scope=producer.scope
    )
    app.dependency_overrides[get_workspace_context] = lambda: ctx
    app.dependency_overrides[get_execution_content_service] = lambda: content
    params = {"version": 1, "run_id": str(producer.run_id), "step_id": step.step_id, "at": view.at}
    path = f"/api/execution-artifacts/{artifact.id}/content"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        assert (await http.get(path, params={**params, "at": old.at})).status_code == 404
        assert (await http.get(path, params={**params, "version": 2})).status_code == 404
        for presentation, expected in [(True, sanitize_html_for_preview(raw)), (False, raw)]:
            cursor, parts = None, []
            while True:
                query = {**params, "presentation": str(presentation).lower()}
                if cursor:
                    query["cursor"] = cursor
                response = await http.get(path, params=query)
                assert response.status_code == 200, response.text
                page = response.json()["data"]
                assert page["content_type"] == "text/html"
                parts.append(page["content"])
                assert len(page["content"].encode()) <= 65536
                cursor = page["next_cursor"]
                assert page["truncated"] == bool(cursor)
                if not cursor:
                    break
            assert len(parts) > 1
            assert "".join(parts) == expected
        provenance = (
            await http.get(f"/api/artifacts/{artifact.id}/provenance", params={"version": 1})
        ).json()["data"]
        assert provenance[0]["producer_run_id"] == str(producer.run_id)
        assert "content_ref" not in provenance[0]
        assert "evidence" not in provenance[0]
        first = (await http.get(path, params=params)).json()["data"]
        async with execution_admin_session() as db:
            await db.execute(
                text("UPDATE sessions SET owner_user_id=NULL WHERE id=:id"), {"id": session_id}
            )
            await db.commit()
        revoked = await http.get(path, params={**params, "cursor": first["next_cursor"]})
        assert revoked.status_code == 404


async def test_fixed_binary_source_and_step_content_recheck_revoked_file():
    import asyncio
    import json
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime
    from io import BytesIO
    from types import SimpleNamespace

    from app.application.services.execution_content_service import ExecutionContentService
    from app.application.services.execution_view_service import ExecutionViewService
    from app.application.services.file_service import FileService
    from app.composition.execution_content import build_execution_event_service
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.external.file_storage import FileUploadPayload
    from app.domain.models.scope import WorkspaceContext
    from app.infrastructure.execution.postgres_execution_content import ExecutionContentWriter
    from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
    from app.infrastructure.external.file_storage.minio_file_storage import MinioFileStorage
    from app.infrastructure.repositories.db_execution_content_repository import (
        DBExecutionContentRepository,
    )
    from app.infrastructure.repositories.db_file_repository import DBFileRepository
    from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository
    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.execution_view_routes import router, stream_events
    from app.interfaces.service_dependencies import get_execution_content_service

    _, producer, _, handler = await production_run()

    @asynccontextmanager
    async def uow():
        async with execution_admin_session() as db:
            yield SimpleNamespace(
                file=DBFileRepository(db),
                resource_pins=DBResourcePinRepository(db),
                execution_content=DBExecutionContentRepository(db),
                commit=db.commit,
            )

    class Transport:
        def put_object(self, **kwargs):
            self.body = kwargs["data"].read()

        def get_object(self, *args):
            return BytesIO(self.body)

        def remove_object(self, *args):
            self.body = b""

    transport = Transport()
    files = FileService(uow, MinioFileStorage("bucket", SimpleNamespace(client=transport), uow))
    binary = b"\x00\xff\xfe" + b"bytes" * 20000
    file = await files.file_storage.upload_file(
        FileUploadPayload(
            file=BytesIO(binary),
            filename="fixed.bin",
            size=len(binary),
            owner_user_id=producer.scope.user_id,
        )
    )
    command_id = uuid4()
    auth = AuthorizationContext.system("f08-source")
    writer = ExecutionContentWriter(
        session_factory=execution_admin_session, authorization=auth, objects=None
    )
    await writer.record(
        producer,
        command_id=command_id,
        phase="output",
        value={"text": "汉🙂" * 20000, "api_key": "do-not-leak"},
        attachment_ids=[file.id],
    )
    envelope = CommandEnvelope(
        command_id=command_id,
        command_type="CompleteActivity",
        command_schema_version=2,
        stream_type="run",
        stream_id=str(producer.run_id),
        owner_user_id=producer.scope.user_id,
        team_id=None,
        correlation_id=producer.run_id,
        causation_id=None,
        issued_at=datetime.now(UTC),
        payload={
            "activity_id": str(producer.activity_id),
            "generation": 0,
            "claim_generation": 1,
            "result_ref": None,
            "result_summary": "summary",
        },
    )
    assert (await handler.handle(envelope)).status == "accepted"
    # Materialized view with an empty public feed is a valid recovery start.
    from tests.app.infrastructure.execution.test_postgres_execution_view import write

    await write(producer.run_id, producer.scope, 1, {"family": "agent", "status": "running"})
    event_service = build_execution_event_service(
        settings=load_deployment_settings(),
        resources=SimpleNamespace(
            postgres=SimpleNamespace(session_factory=execution_admin_session)
        ),
        authorization=auth,
    )
    assert not (await event_service.list_events(producer.scope, producer.run_id)).events

    class Connected:
        async def is_disconnected(self):
            return False

    ctx = WorkspaceContext(
        principal=Principal(user_id=producer.scope.user_id), scope=producer.scope
    )
    stream = await stream_events(
        Connected(), producer.run_id, after=None, last_event_id=None, ctx=ctx, service=event_service
    )
    projector = PostgresFormalProjector(session_factory=kernel_session, authorization=auth)
    await projector.run_once(producer.scope, limit=1000)
    try:
        emitted = await asyncio.wait_for(anext(stream.body_iterator), 3)
        assert emitted.id
        assert json.loads(emitted.data)["run_id"] == str(producer.run_id)
    finally:
        await stream.body_iterator.aclose()
    views = ExecutionViewService(
        PostgresExecutionView(session_factory=execution_admin_session, authorization=auth),
        cursor_secret=b"0123456789abcdef",
    )
    view = await views.get_view(producer.scope, producer.run_id)
    step = next(s for s in view.steps if s.output_ref)
    citation = step.citation_refs[0]
    content = ExecutionContentService(
        uow, views, None, cursor_secret=b"0123456789abcdef", files=files
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_workspace_context] = lambda: ctx
    app.dependency_overrides[get_execution_content_service] = lambda: content
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        source_path = f"/api/execution-sources/{citation.citation_id}"
        response = await http.get(source_path + "/download")
        assert response.status_code == 200, response.text
        assert response.content == binary
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert (await http.get(source_path + "/content")).status_code == 409
        body_path = f"/api/execution-runs/{producer.run_id}/steps/{step.step_id}/content"
        params = {"at": view.at}
        page = (await http.get(body_path, params=params)).json()["data"]
        assert page["redacted"]
        assert "do-not-leak" not in page["content"]
        assert page["next_cursor"]
        await files.delete_file(file.id, producer.scope, force=True)
        assert (await http.get(source_path + "/download")).status_code == 404
        assert (
            await http.get(body_path, params={**params, "cursor": page["next_cursor"]})
        ).status_code == 404


async def test_existing_fixed_knowledge_source_and_pin_revocation_contract():
    from tests.app.application.services.test_execution_content_production import (
        test_real_worker_catalog_captures_full_inputs_outputs_and_owned_fixed_citation,
    )

    await test_real_worker_catalog_captures_full_inputs_outputs_and_owned_fixed_citation()


async def test_existing_f04_immutable_step_query_contract():
    from tests.app.infrastructure.execution.test_postgres_execution_view import (
        test_step_pages_remain_historical_after_live_changes_and_scope_absence,
    )

    await test_step_pages_remain_historical_after_live_changes_and_scope_absence()
