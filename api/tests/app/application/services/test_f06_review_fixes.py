"""Concrete public-content review regressions through actual DB and route paths."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.application.services.artifact_service import ArtifactService, sanitize_html_for_preview
from app.application.services.execution_content_service import ExecutionContentService
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.infrastructure.repositories.db_artifact_provenance_repository import (
    DBArtifactProvenanceRepository,
)
from app.infrastructure.repositories.db_artifact_repository import DBArtifactRepository
from app.infrastructure.repositories.db_session_repository import DBSessionRepository
from tests.app.application.services.test_artifact_provenance_postgres import Objects, seed, uow
from tests.app.artifact_test_support import UnitOfWorkUploadIntents
from tests.app.execution_test_support import execution_admin_session

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("postgres_integration")]


@asynccontextmanager
async def readers():
    async with execution_admin_session() as db:
        yield SimpleNamespace(
            artifact=DBArtifactRepository(db),
            artifact_provenance=DBArtifactProvenanceRepository(db),
            session=DBSessionRepository(db),
        )


@pytest.mark.parametrize("paged", [False, True])
@pytest.mark.parametrize("kind", ["web", "doc"])
async def test_current_html_preview_export_is_complete_safe_and_truthful(paged, kind):
    owner, session_id = await seed()
    scope = OwnerScope.personal(owner)
    objects = Objects()
    from sqlalchemy import text

    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE sessions SET status='running' WHERE id=:id"), {"id": session_id}
        )
        await db.commit()
    # Active markup straddles the old 64 KiB boundary as well as the prefix.
    prefix = '<script>window.bad=1</script><p onclick="window.bad=2">'
    body = (
        prefix
        + ("a" * (65536 - len(prefix.encode()) - 4))
        + "<script>window.bad=3</script>"
        + ("汉🙂" * 1000)
        + "</p>END"
    )
    artifact = await ArtifactService(
        uow, objects, upload_intents=UnitOfWorkUploadIntents(uow)
    ).write_content(session_id, None, kind, "preview", body, verify_upload=False)
    service = ArtifactService(readers, objects, upload_intents=UnitOfWorkUploadIntents(readers))
    content = ExecutionContentService(
        readers, None, service, cursor_secret=b"f06-preview-test-secret"
    )
    ctx = WorkspaceContext(principal=Principal(user_id=owner), scope=scope)
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.artifact_routes import router
    from app.interfaces.service_dependencies import (
        get_artifact_service,
        get_execution_content_service,
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: ctx
    app.dependency_overrides[get_artifact_service] = lambda: service
    app.dependency_overrides[get_execution_content_service] = lambda: content
    cursor = None
    parts = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        while True:
            params = {"version": 1}
            if paged:
                params["limit_bytes"] = 65536
            if cursor:
                params["cursor"] = cursor
            response = await client.get("/artifacts/" + artifact.id + "/content", params=params)
            assert response.status_code == 200, response.text
            page = SimpleNamespace(**response.json()["data"])
            if kind == "web":
                assert "<script" not in page.content
                assert "onclick=" not in page.content
            assert page.incomplete == page.truncated
            parts.append(page.content)
            if not page.next_cursor:
                break
            cursor = page.next_cursor
    # This is the unchanged workbench export contract: complete content string.
    assert "".join(parts) == (sanitize_html_for_preview(body) if kind == "web" else body)
    if not paged:
        assert len(parts) == 1
        assert len(parts[0].encode()) > 65536
        assert page.incomplete is False
    raw = []
    cursor = None
    while True:
        page = await content.read_artifact(scope, artifact.id, 1, cursor=cursor)
        raw.append(page.content)
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    assert "".join(raw) == body


@pytest.mark.parametrize("failure", ["raise", "hang"])
async def test_scheduled_cleanup_old_upload_failure_cannot_starve_retirement(failure, monkeypatch):
    import asyncio
    from datetime import datetime, timedelta

    from sqlalchemy import text

    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )
    from app.infrastructure.repositories.db_session_repository import DBSessionRepository

    owner, session_id = await seed()
    scope = OwnerScope.personal(owner)

    class FailingObjects(Objects):
        failed_key = None

        async def delete_bytes(self, key):
            if key == self.failed_key:
                if failure == "raise":
                    raise OSError("old upload is unavailable")
                await asyncio.Event().wait()
            await super().delete_bytes(key)

    objects = FailingObjects()
    artifacts = ArtifactService(uow, objects, upload_intents=UnitOfWorkUploadIntents(uow))
    old = await artifacts.write_content(
        session_id, None, "doc", "old", "failed-upload", verify_upload=False
    )
    retired = await artifacts.write_content(
        session_id, None, "doc", "retire", "must-clean", verify_upload=False
    )
    # A real source delete supplies retirement authority; old upload remains
    # pending while the other intent has already been successfully settled.
    objects.failed_key = old.version_refs[0]
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE artifact_upload_intents SET cleaned_at=CURRENT_TIMESTAMP WHERE artifact_id=:id"
            ),
            {"id": retired.id},
        )
        await db.execute(
            text("UPDATE sessions SET deleted_at=CURRENT_TIMESTAMP WHERE id=:id"),
            {"id": session_id},
        )
        await DBSessionRepository(db).purge(session_id, scope=scope, force=True)
        await db.commit()

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=2)

    monkeypatch.setattr("app.infrastructure.execution.postgres_artifact_provenance.datetime", Later)
    maintenance = ArtifactProvenanceMaintenance(
        session_factory=execution_admin_session,
        authorization=AuthorizationContext.system("f06-fix1"),
        objects=objects,
        handler=None,
    )
    # Production timeout is bounded; shorten the same setting for this race.
    maintenance._object_delete_timeout = 0.05
    async with asyncio.timeout(3):
        await maintenance.process_pending()
    assert retired.version_refs[0] not in objects.data
    assert old.version_refs[0] in objects.data
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text("SELECT cleaned_at FROM artifact_upload_intents WHERE artifact_id=:id"),
                {"id": old.id},
            )
            is None
        )
    objects.failed_key = None
    await maintenance.process_pending()
    assert old.version_refs[0] not in objects.data


async def test_bound_file_copy_pin_becomes_unavailable_after_actual_force_delete():
    from datetime import UTC, datetime
    from io import BytesIO
    from uuid import uuid4

    from sqlalchemy import text

    from app.application.ports.execution_view import ViewNotFound
    from app.application.services.execution_view_service import ExecutionViewService
    from app.application.services.file_service import FileService
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.external.file_storage import FileUploadPayload
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.resource_pin import ResourceIdentity, ResourceUnavailable
    from app.infrastructure.execution.postgres_execution_content import ExecutionContentWriter
    from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from app.infrastructure.external.file_storage.minio_file_storage import MinioFileStorage
    from app.infrastructure.repositories.db_execution_content_repository import (
        DBExecutionContentRepository,
    )
    from app.infrastructure.repositories.db_file_repository import DBFileRepository
    from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository
    from tests.app.application.services.test_artifact_provenance_postgres import production_run

    session_id, producer, _, handler = await production_run()
    scope = producer.scope

    @asynccontextmanager
    async def file_uow():
        async with execution_admin_session() as db:
            yield SimpleNamespace(
                file=DBFileRepository(db),
                execution_content=DBExecutionContentRepository(db),
                commit=db.commit,
            )

    class Client:
        def __init__(self):
            self.data = {}

        def put_object(self, **kwargs):
            self.data[kwargs["object_name"]] = kwargs["data"].read()

        def remove_object(self, bucket, key):
            self.data.pop(key, None)

    client = Client()
    storage = MinioFileStorage("bucket", SimpleNamespace(client=client), file_uow)
    file = await storage.upload_file(
        FileUploadPayload(
            file=BytesIO(b"fixed attachment"),
            filename="fixed.txt",
            size=16,
            owner_user_id=scope.user_id,
        )
    )
    command_id = uuid4()
    auth = AuthorizationContext.system("f06-file-copy")
    reference = await ExecutionContentWriter(
        session_factory=execution_admin_session, authorization=auth, objects=None
    ).record(
        producer,
        command_id=command_id,
        phase="output",
        value={"body": "copied attachment"},
        attachment_ids=[file.id],
    )
    envelope = CommandEnvelope(
        command_id=command_id,
        command_type="CompleteActivity",
        command_schema_version=2,
        stream_type="run",
        stream_id=str(producer.run_id),
        owner_user_id=scope.user_id,
        team_id=None,
        correlation_id=producer.run_id,
        causation_id=None,
        issued_at=datetime.now(UTC),
        payload={
            "activity_id": str(producer.activity_id),
            "generation": 0,
            "claim_generation": 1,
            "result_ref": None,
            "result_summary": "copied",
        },
    )
    assert (await handler.handle(envelope)).status == "accepted"
    await PostgresFormalProjector(
        session_factory=execution_admin_session, authorization=auth
    ).run_once(scope, limit=1000)
    views = ExecutionViewService(
        PostgresExecutionView(session_factory=execution_admin_session, authorization=auth),
        cursor_secret=b"f06-file-copy-view",
    )
    view = await views.get_view(scope, producer.run_id)
    step = next(s for s in view.steps if s.output_ref)
    reader = ExecutionContentService(file_uow, views, None, cursor_secret=b"f06-file-copy-reader")
    assert (
        await reader.read_step_content(scope, producer.run_id, step.step_id, view.at)
    ).availability == "available"
    async with execution_admin_session() as db:
        digest = await db.scalar(
            text("SELECT content_digest FROM execution_public_content WHERE content_id=:id"),
            {"id": reference.content_id},
        )
        pin = ResourceIdentity(
            resource_kind="execution_content",
            resource_id=reference.content_id,
            resource_version=digest,
        )
        await DBResourcePinRepository(db).acquire(scope, "session", session_id, [pin])
        await db.commit()
    await FileService(file_uow, storage).delete_file(file.id, scope, force=True)
    with pytest.raises(ViewNotFound):
        await reader.read_step_content(scope, producer.run_id, step.step_id, view.at)
    async with execution_admin_session() as db:
        repo = DBResourcePinRepository(db)
        assert not (await repo.validate(scope, "session", session_id, [pin]))[0].available
        with pytest.raises(ResourceUnavailable):
            await repo.acquire(scope, "session", session_id, [pin])
