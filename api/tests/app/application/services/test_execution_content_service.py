"""F06 fixed references must be produced at the exact persisted cut."""

import pytest
from sqlalchemy import text

from tests.app.application.services.test_artifact_provenance_postgres import (
    Objects,
    kernel_session,
    production_run,
    uow,
)
from tests.app.artifact_test_support import UnitOfWorkUploadIntents
from tests.app.execution_test_support import execution_admin_session


@pytest.mark.asyncio
@pytest.mark.usefixtures("postgres_integration")
async def test_exact_producer_step_attaches_artifact_only_at_new_cut():
    from app.application.execution.view_facts import attempt_key
    from app.application.services.artifact_service import ArtifactService
    from app.application.services.execution_view_service import ExecutionViewService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )
    from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector

    session_id, producer, _command, handler = await production_run()
    authorization = AuthorizationContext.system("f06")
    objects = Objects()
    projector = PostgresFormalProjector(session_factory=kernel_session, authorization=authorization)
    views = ExecutionViewService(
        PostgresExecutionView(session_factory=execution_admin_session, authorization=authorization),
        cursor_secret=b"f06-content-test-secret",
    )
    await projector.run_once(producer.scope, limit=1000)
    before = await views.get_view(producer.scope, producer.run_id)
    service = ArtifactService(uow, objects, upload_intents=UnitOfWorkUploadIntents(uow))
    first = await service.write_content(
        session_id, None, "doc", "first", "one", producer=producer, verify_upload=False
    )
    maintenance = ArtifactProvenanceMaintenance(
        session_factory=execution_admin_session,
        authorization=authorization,
        objects=objects,
        handler=handler,
    )
    await maintenance.process_pending()
    await projector.run_once(producer.scope, limit=1000)
    after = await views.get_view(producer.scope, producer.run_id)
    step_id = attempt_key(str(producer.activity_id), 0, 1)
    step = await views.get_step(producer.scope, producer.run_id, step_id, after.at)
    assert {(r.artifact_id, r.version) for r in (step.artifact_refs or [])} == {(first.id, 1)}
    old = await views.get_step(producer.scope, producer.run_id, step_id, before.at)
    assert not old.artifact_refs
    cut = await views.get_step_cut(producer.scope, producer.run_id, step_id, after.at)
    assert cut.step == step
    assert cut.at == after.at
    async with uow() as unit:
        records = await unit.artifact_provenance.get_version(producer.scope, first.id, 1)
    assert cut.boundary.formal_position >= records[0].boundary
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from app.application.services.execution_content_service import ExecutionContentService
    from app.infrastructure.repositories.db_artifact_provenance_repository import (
        DBArtifactProvenanceRepository,
    )
    from app.infrastructure.repositories.db_artifact_repository import DBArtifactRepository
    from app.infrastructure.repositories.db_session_repository import DBSessionRepository

    @asynccontextmanager
    async def readers():
        async with execution_admin_session() as db:
            yield SimpleNamespace(
                artifact=DBArtifactRepository(db),
                artifact_provenance=DBArtifactProvenanceRepository(db),
                session=DBSessionRepository(db),
            )

    content = ExecutionContentService(
        readers,
        views,
        ArtifactService(readers, objects, upload_intents=UnitOfWorkUploadIntents(readers)),
        cursor_secret=b"f06-artifact-byte-secret",
    )
    public = await content.get_public_provenance(
        producer.scope, first.id, 1, run_id=producer.run_id, at=after.at
    )
    assert len(public) == 1
    async with execution_admin_session() as db:
        persisted = (
            await db.execute(
                text(
                    "SELECT observed_order, observed_at FROM execution_view_observations WHERE run_id=:run AND event_id=:event AND source_kind='formal'"
                ),
                {"run": producer.run_id, "event": records[0].produced_event_id},
            )
        ).one()
    assert public[0]["production_order"] == persisted.observed_order
    assert public[0]["production_observed_at"] == persisted.observed_at
    assert "boundary" not in public[0]
    assert "evidence" not in public[0]
    assert (
        await content.get_public_provenance(
            producer.scope, first.id, 1, run_id=producer.run_id, at=before.at
        )
        == []
    )
    selected = await content.read_artifact(
        producer.scope, first.id, 1, run_id=producer.run_id, step_id=step_id, at=after.at
    )
    assert selected.content == "one"
    from app.application.ports.execution_view import ViewNotFound

    with pytest.raises(ViewNotFound):
        await content.read_artifact(
            producer.scope, first.id, 1, run_id=producer.run_id, step_id=step_id, at=before.at
        )
    second = await service.write_content(
        session_id, first.id, "doc", "second", "new version", verify_upload=False
    )
    assert len(second.version_refs) == 2
    assert (
        await content.read_artifact(
            producer.scope, first.id, 1, run_id=producer.run_id, step_id=step_id, at=after.at
        )
    ).content == "one"
    with pytest.raises(ViewNotFound):
        await content.read_artifact(
            producer.scope, first.id, 2, run_id=producer.run_id, step_id=step_id, at=after.at
        )
    with pytest.raises(ViewNotFound):
        await content.read_artifact(producer.scope, first.id, 3)
    from app.domain.models.scope import OwnerScope

    with pytest.raises(ViewNotFound):
        await content.read_artifact(OwnerScope.personal("wrong-owner"), first.id, 1)
    from app.domain.models.resource_pin import ResourceUnavailable

    objects.data[first.version_refs[0]] = b"drift"
    with pytest.raises(ResourceUnavailable):
        await content.read_artifact(producer.scope, first.id, 1)


def test_missing_chunk_falls_back_inside_same_revision():
    import importlib.util

    assert importlib.util.find_spec("app.application.services.execution_content_service"), (
        "fixed reader missing"
    )
    from app.application.services.execution_content_service import citation_locator

    assert citation_locator({"chunk_id": None, "page_no": 4}) == ("page", 4)
    assert citation_locator({"chunk_id": "c7", "page_no": 4}) == ("chunk", "c7")


@pytest.mark.asyncio
@pytest.mark.usefixtures("postgres_integration")
@pytest.mark.parametrize("legacy", [False, True])
async def test_snapshot_binds_only_matching_new_command_and_never_late_to_old_cut(
    monkeypatch, legacy
):
    import importlib.util

    assert importlib.util.find_spec("app.infrastructure.execution.postgres_execution_content"), (
        "trusted content producer missing"
    )
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.application.services.execution_view_service import ExecutionViewService
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_execution_content import ExecutionContentWriter
    from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector

    session_id, producer, _command, handler = await production_run()
    auth = AuthorizationContext.system("f06")
    writer = ExecutionContentWriter(
        session_factory=execution_admin_session, authorization=auth, objects=None
    )
    command_id = uuid4()
    from app.infrastructure.execution import postgres_execution_content as capture_module

    original_sanitizer = capture_module.sanitize_content
    if legacy:
        # Emulate an already persisted pre-fix body, without rewriting it later.
        monkeypatch.setattr(capture_module, "sanitize_content", lambda value: value)
    reference = await writer.record(
        producer,
        command_id=command_id,
        phase="output",
        value={
            "answer": "汉字🙂" * 30000,
            "authorization": "Bearer cannot-leak",
            "storage_ref": "execution/private.json",
            "request": {"history_refs": ["execution/results/example-activity/example-digest.json"]},
            "data": '{"api_key": "example-sensitive-value"}',
            "narrative": 'tool result: {"password": "quoted-sensitive-value"}',
        },
    )
    monkeypatch.setattr(capture_module, "sanitize_content", original_sanitizer)
    async with execution_admin_session() as db:
        persisted_before = (
            await db.execute(
                text(
                    "SELECT body,content_digest,redacted FROM execution_public_content WHERE content_id=:id"
                ),
                {"id": reference.content_id},
            )
        ).one()
        assert persisted_before.redacted is (not legacy)
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
    projector = PostgresFormalProjector(session_factory=execution_admin_session, authorization=auth)
    await projector.run_once(producer.scope, limit=1000)
    views = ExecutionViewService(
        PostgresExecutionView(session_factory=execution_admin_session, authorization=auth),
        cursor_secret=b"f06-content-test-secret",
    )
    view = await views.get_view(producer.scope, producer.run_id)
    step = next(s for s in view.steps if s.attempt_id)
    assert step.output_ref.content_id == reference.content_id
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.domain.models.scope import OwnerScope, Principal
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.infrastructure.security.test_execution_view_rls import factory

    engine = create_async_engine(load_deployment_settings().sqlalchemy_database_uri)
    try:
        for user, expected in ((producer.scope.user_id, 1), ("unrelated-reader", 0)):
            async with factory(engine)() as db:
                await configure_session_authorization(
                    db,
                    AuthorizationContext.for_principal(
                        Principal(user_id=user), scope=OwnerScope.personal(user)
                    ),
                )
                assert (
                    await db.scalar(
                        text("SELECT count(*) FROM execution_public_content WHERE content_id=:id"),
                        {"id": reference.content_id},
                    )
                    == expected
                )
                assert (
                    await db.scalar(
                        text(
                            "SELECT count(*) FROM execution_content_bindings WHERE content_id=:id"
                        ),
                        {"id": reference.content_id},
                    )
                    == expected
                )
    finally:
        await engine.dispose()
    assert step.input_ref is None
    from app.domain.models.resource_pin import ResourceIdentity
    from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository

    async with execution_admin_session() as db:
        digest = await db.scalar(
            text("SELECT content_digest FROM execution_public_content WHERE content_id=:id"),
            {"id": reference.content_id},
        )
        pin = ResourceIdentity(
            resource_kind="execution_content",
            resource_id=str(reference.content_id),
            resource_version=digest,
        )
        repo = DBResourcePinRepository(db)
        await repo.acquire(producer.scope, "session", session_id, [pin])
        assert (await repo.validate(producer.scope, "session", session_id, [pin]))[0].available
        await db.rollback()
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from app.application.services.execution_content_service import ExecutionContentService
    from app.infrastructure.repositories.db_execution_content_repository import (
        DBExecutionContentRepository,
    )

    @asynccontextmanager
    async def content_uow():
        async with execution_admin_session() as db:
            yield SimpleNamespace(execution_content=DBExecutionContentRepository(db))

    reader = ExecutionContentService(
        content_uow, views, None, cursor_secret=b"f06-body-cursor-secret"
    )
    page = await reader.read_step_content(
        producer.scope, producer.run_id, step.step_id, view.at, limit_bytes=65536
    )
    assert page.redacted
    assert page.truncated
    assert len(page.content.encode()) <= 65536
    text_parts = [page.content]
    while page.next_cursor:
        page = await reader.read_step_content(
            producer.scope,
            producer.run_id,
            step.step_id,
            view.at,
            cursor=page.next_cursor,
            limit_bytes=65536,
        )
        text_parts.append(page.content)
    import json

    decoded = json.loads("".join(text_parts))
    assert decoded["request"]["history_refs"] == "[redacted]"
    assert "example-sensitive-value" not in decoded["data"]
    assert "quoted-sensitive-value" not in decoded["narrative"]
    assert decoded["answer"] == "汉字🙂" * 30000
    assert decoded["authorization"] == "[redacted]"
    assert decoded["storage_ref"] == "[redacted]"
    from app.application.ports.execution_view import ViewCursorInvalid

    with pytest.raises(ViewCursorInvalid):
        await reader.read_step_content(
            producer.scope, producer.run_id, step.step_id, view.at, limit_bytes=1
        )
    # Immutable body cannot be replaced even through trusted retry paths.
    from app.domain.models.resource_pin import ResourceUnavailable

    with pytest.raises(ResourceUnavailable):
        await writer.record(
            producer, command_id=command_id, phase="output", value={"answer": "replacement"}
        )
    # An input capture after the completion command cannot acquire its event.
    await writer.record(producer, command_id=command_id, phase="input", value={"late": "input"})
    await handler.handle(envelope)
    await projector.run_once(producer.scope, limit=1000)
    again = await views.get_view(producer.scope, producer.run_id, at=view.at)
    assert again == view
    async with execution_admin_session() as db:
        assert (
            await db.execute(
                text(
                    "SELECT body,content_digest,redacted FROM execution_public_content WHERE content_id=:id"
                ),
                {"id": reference.content_id},
            )
        ).one() == persisted_before
    old_identity = {
        "kind": "step",
        "run": str(producer.run_id),
        "step": step.step_id,
        "at": view.at,
        "content_kind": "output",
    }
    old_cursor = reader._cursor(
        producer.scope, old_identity, {"offset": 4, "digest": persisted_before.content_digest}
    )
    with pytest.raises(ViewCursorInvalid):
        await reader.read_step_content(
            producer.scope, producer.run_id, step.step_id, view.at, cursor=old_cursor
        )


def test_typed_citations_preserve_owning_knowledge_base_and_full_identity():
    from app.domain.models.knowledge_citation import KnowledgeCitation, deduplicate_citations

    common = {"version_id": "v1", "document_revision_id": "r1", "doc_id": "d1", "chunk_id": "c1"}
    a = KnowledgeCitation(knowledge_base_id="kb1", **common)
    b = KnowledgeCitation(knowledge_base_id="kb2", **common)
    assert len(deduplicate_citations([a, b, a])) == 2
    assert a.knowledge_base_id == "kb1"
    from app.application.dto.execution_view import CitationReference

    ref = CitationReference(
        citation_id="trusted", knowledge_base_id="kb1", availability="available", **common
    )
    assert ref.knowledge_base_id == "kb1"
