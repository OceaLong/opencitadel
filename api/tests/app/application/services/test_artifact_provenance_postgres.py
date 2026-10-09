import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from core.config import load_deployment_settings
from tests.app.artifact_test_support import UnitOfWorkUploadIntents
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_admin_database_uri,
    execution_admin_session,
    execution_kernel_database_uri,
)

pytestmark = pytest.mark.usefixtures("postgres_integration")


class Objects:
    def __init__(self):
        self.data = {}

    async def put_bytes(self, key, data):
        self.data[key] = data

    async def get_bytes(self, key):
        return self.data[key]

    async def delete_bytes(self, key):
        self.data.pop(key, None)


@asynccontextmanager
async def uow():
    from app.infrastructure.repositories.db_artifact_provenance_repository import (
        DBArtifactProvenanceRepository,
    )
    from app.infrastructure.repositories.db_artifact_repository import DBArtifactRepository

    async with execution_admin_session() as db:
        yield SimpleNamespace(
            artifact=DBArtifactRepository(db),
            artifact_provenance=DBArtifactProvenanceRepository(db),
            commit=db.commit,
        )


@asynccontextmanager
async def kernel_session():
    """Project artifact bindings through the real execution-kernel login."""
    settings = load_deployment_settings()
    # Isolated integration tests redirect the migration target, not the API URI.
    # Use that invocation-owned database while retaining the kernel role credentials.
    target = make_url(execution_admin_database_uri())
    kernel = make_url(execution_kernel_database_uri())
    engine = create_async_engine(
        kernel.set(host=target.host, port=target.port, database=target.database)
    )
    factory = authenticated_session_factory(
        engine, signing_secret=settings.database_authorization_signing_secret
    )
    try:
        async with factory() as db:
            yield db
    finally:
        await engine.dispose()


async def seed():
    owner, session_id = "f05-" + uuid4().hex, str(uuid4())
    async with execution_admin_session() as db:
        await db.execute(
            text("INSERT INTO users(id,email,username) VALUES (:id,:email,:id)"),
            {"id": owner, "email": owner + "@test.invalid"},
        )
        await db.execute(
            text("INSERT INTO sessions(id,owner_user_id) VALUES (:id,:owner)"),
            {"id": session_id, "owner": owner},
        )
        await db.commit()
    return owner, session_id


@pytest.mark.asyncio
async def test_concurrent_writes_preserve_versions_and_unknown_producers():
    import importlib.util

    assert importlib.util.find_spec(
        "app.infrastructure.repositories.db_artifact_provenance_repository"
    ), "atomic provenance repository missing"
    from app.application.services.artifact_service import ArtifactService

    owner, session_id = await seed()
    storage = Objects()
    service = ArtifactService(uow, storage, upload_intents=UnitOfWorkUploadIntents(uow))
    first = await service.write_content(
        session_id, None, "doc", "title", "one", verify_upload=False
    )
    await asyncio.gather(
        *(
            service.write_content(session_id, first.id, "doc", "title", value, verify_upload=False)
            for value in ("two", "three")
        )
    )
    async with uow() as unit:
        artifact = await unit.artifact.get_by_id(first.id)
        assert len(artifact.version_refs) == len(set(artifact.version_refs)) == 3
        from app.domain.models.scope import OwnerScope

        records = await unit.artifact_provenance.get_version(
            OwnerScope.personal(owner), first.id, 1
        )
        assert len(records) == 1
        assert records[0].evidence_kind == "unknown"
    assert {storage.data[key] for key in artifact.version_refs} == {b"one", b"two", b"three"}


async def production_run(*, activity_type="tool.call", scope=None):
    from datetime import UTC, datetime, timedelta

    from app.domain.execution.commands import CommandEnvelope
    from app.domain.execution.run import RunAggregate
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
    from tests.app.execution_test_support import run_policy_snapshot_json

    if scope is None:
        owner, session_id = await seed()
        scope = OwnerScope.personal(owner)
    else:
        owner, session_id = scope.user_id, str(uuid4())
        async with execution_admin_session() as db:
            await db.execute(
                text("INSERT INTO sessions(id,owner_user_id,team_id) VALUES(:id,:owner,:team)"),
                {
                    "id": session_id,
                    "owner": None if scope.team_id else owner,
                    "team": scope.team_id,
                },
            )
            await db.commit()
    run, activity = uuid4(), uuid4()
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=execution_admin_session,
        aggregates={"run": RunAggregate()},
        authorization=AuthorizationContext.system("f05"),
    )

    async def command(name, payload, schema=1):
        envelope = CommandEnvelope(
            command_id=uuid4(),
            command_type=name,
            command_schema_version=schema,
            stream_type="run",
            stream_id=str(run),
            owner_user_id=None if scope.team_id else owner,
            team_id=scope.team_id,
            correlation_id=run,
            causation_id=None,
            issued_at=datetime.now(UTC),
            payload=payload,
        )
        result = await handler.handle(envelope)
        assert result.status == "accepted", result
        return envelope, result

    await command(
        "CreateRun",
        {
            "family": "agent",
            "source_entity_type": "session",
            "source_entity_id": session_id,
            "semantic_payload": {},
            "policy_snapshot": run_policy_snapshot_json("agent"),
        },
    )
    await command("StartRun", {})
    await command(
        "RequestActivity",
        {
            "activity_id": str(activity),
            "activity_type": activity_type,
            "timeout_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "input_digest": "f05",
            "input_payload": {},
            "public_data": {"tool_name": "artifact_write"},
        },
        2,
    )
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE execution_activity_tasks SET claim_generation=1,status='call_started' WHERE activity_id=:id"
            ),
            {"id": activity},
        )
        await db.commit()
    await command(
        "MarkActivityCallStarted",
        {"activity_id": str(activity), "generation": 0, "claim_generation": 1},
        2,
    )
    from app.domain.models.artifact_provenance import ArtifactProducer

    producer = ArtifactProducer(
        scope=scope,
        run_id=run,
        activity_id=activity,
        generation=0,
        claim_generation=1,
    )
    return session_id, producer, command, handler


@pytest.mark.asyncio
async def test_pending_production_retries_bind_once_after_terminal_without_rewriting_history():
    import importlib.util

    assert importlib.util.find_spec("app.infrastructure.execution.postgres_artifact_provenance"), (
        "durable artifact reconciliation missing"
    )
    from app.application.services.artifact_service import ArtifactService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector

    session_id, producer, command, handler = await production_run()
    objects = Objects()
    service = ArtifactService(uow, objects, upload_intents=UnitOfWorkUploadIntents(uow))
    first, second = await asyncio.gather(
        *(
            service.write_content(
                session_id, None, "doc", "title", "durable", producer=producer, verify_upload=False
            )
            for _ in range(2)
        )
    )
    assert first.id == second.id
    assert len(first.version_refs) == 1
    async with uow() as unit:
        records = await unit.artifact_provenance.get_version(producer.scope, first.id, 1)
        assert records[0].binding_status == "pending"
        assert records[0].producer_run_id is None
    await command("FailRun", {"failure_code": "later_failure"})
    projector = PostgresFormalProjector(
        session_factory=kernel_session, authorization=AuthorizationContext.system("f05")
    )
    await projector.run_once(producer.scope, limit=1000)
    from app.application.services.execution_view_service import ExecutionViewService
    from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView

    views = ExecutionViewService(
        PostgresExecutionView(
            session_factory=execution_admin_session,
            authorization=AuthorizationContext.system("f05"),
        ),
        cursor_secret=b"f05-historical-cursor-secret",
    )
    async with execution_admin_session() as db:
        terminal_at = await db.scalar(
            text("SELECT terminal_at FROM execution_run_projection WHERE run_id=:id"),
            {"id": producer.run_id},
        )
    before = await views.get_view(producer.scope, producer.run_id)
    assert before.run.status == "failed"
    assert not before.artifacts
    maintenance = ArtifactProvenanceMaintenance(
        session_factory=execution_admin_session,
        authorization=AuthorizationContext.system("f05"),
        objects=objects,
        handler=handler,
    )
    await maintenance.process_pending()
    await maintenance.process_pending()
    projector = PostgresFormalProjector(
        session_factory=kernel_session, authorization=AuthorizationContext.system("f05")
    )
    await projector.run_once(producer.scope, limit=1000)
    async with uow() as unit:
        rows = await unit.artifact_provenance.get_version(producer.scope, first.id, 1)
        assert rows[0].binding_status == "bound"
        assert rows[0].producer_run_id == producer.run_id
        assert rows[0].activity_id == producer.activity_id
    async with execution_admin_session() as db:
        events = (
            await db.execute(
                text(
                    "SELECT event_id,position FROM execution_events WHERE stream_id=:run AND event_type='ArtifactVersionProduced'"
                ),
                {"run": str(producer.run_id)},
            )
        ).all()
        assert len(events) == 1
        assert rows[0].produced_event_id == events[0].event_id
    after = await views.get_view(producer.scope, producer.run_id)
    old = await views.get_view(producer.scope, producer.run_id, at=before.at)
    assert old.artifacts == before.artifacts == []
    assert after.run.status == "failed"
    assert after.run.terminal_at == before.run.terminal_at
    assert after.run.duration_ms == before.run.duration_ms
    assert [(x.artifact_id, x.version) for x in after.artifacts] == [(first.id, 1)]
    historic = await views.get_view(producer.scope, producer.run_id, at=after.at)
    assert historic.artifacts == after.artifacts
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text("SELECT terminal_at FROM execution_run_projection WHERE run_id=:id"),
                {"id": producer.run_id},
            )
            == terminal_at
        )


@pytest.mark.asyncio
async def test_failed_database_write_has_durable_cleanup_and_never_deletes_success():
    from datetime import UTC, datetime, timedelta

    from app.application.services.artifact_service import ArtifactService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )

    objects = Objects()
    _owner, session_id = await seed()

    @asynccontextmanager
    async def failing_uow():
        async with uow() as unit:

            async def fail(*args, **kwargs):
                raise RuntimeError("injected DB save failure")

            unit.artifact.save = fail
            yield unit

    with pytest.raises(RuntimeError, match="injected"):
        await ArtifactService(
            failing_uow, objects, upload_intents=UnitOfWorkUploadIntents(failing_uow)
        ).write_content(session_id, None, "doc", "bad", "orphan", verify_upload=False)
    orphan = set(objects.data)
    assert len(orphan) == 1
    good = await ArtifactService(
        uow, objects, upload_intents=UnitOfWorkUploadIntents(uow)
    ).write_content(session_id, None, "doc", "good", "retained", verify_upload=False)
    maintenance = ArtifactProvenanceMaintenance(
        session_factory=execution_admin_session,
        authorization=AuthorizationContext.system("f05"),
        objects=objects,
        handler=None,
    )
    count = await maintenance.cleanup_uploads(
        before=datetime.now(UTC) + timedelta(seconds=1), limit=1000
    )
    assert not (orphan & objects.data.keys())
    assert objects.data[good.storage_ref] == b"retained"
    assert count >= 1
    assert (
        await maintenance.cleanup_uploads(
            before=datetime.now(UTC) + timedelta(seconds=1), limit=1000
        )
        == 0
    )


@pytest.mark.asyncio
async def test_receipt_authority_rejects_forged_command_and_repeat_dedupes_without_inbox_identity():
    from app.application.services.artifact_service import ArtifactService
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
        receipt_payload,
    )

    session_id, producer, _command, handler = await production_run()
    objects = Objects()
    await ArtifactService(uow, objects, upload_intents=UnitOfWorkUploadIntents(uow)).write_content(
        session_id, None, "doc", "title", "receipt", producer=producer, verify_upload=False
    )
    async with execution_admin_session() as db:
        row = (
            (
                await db.execute(
                    text("SELECT * FROM artifact_production_receipts WHERE operation_id=:id"),
                    {"id": producer.operation_id},
                )
            )
            .mappings()
            .one()
        )
    from datetime import UTC, datetime

    forged = CommandEnvelope(
        command_id=uuid4(),
        command_type="RecordArtifactVersionProduced",
        command_schema_version=2,
        stream_type="run",
        stream_id=str(producer.run_id),
        owner_user_id=producer.scope.user_id,
        team_id=None,
        correlation_id=producer.run_id,
        causation_id=None,
        issued_at=datetime.now(UTC),
        payload={**receipt_payload(row), "claim_generation": 2},
    )
    assert (await handler.handle(forged)).status == "rejected"
    maintenance = ArtifactProvenanceMaintenance(
        session_factory=execution_admin_session,
        authorization=AuthorizationContext.system("f05"),
        objects=objects,
        handler=handler,
    )
    await maintenance.process_pending()
    # Exercise durable dedupe with a NEW command identity, stronger than inbox retry.
    repeated = forged.model_copy(update={"command_id": uuid4(), "payload": receipt_payload(row)})
    first = await handler.handle(repeated)
    second = await handler.handle(repeated.model_copy(update={"command_id": uuid4()}))
    assert first.status == second.status == "accepted"
    assert first.first_event_position == second.first_event_position
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM execution_events WHERE stream_id=:run AND event_type='ArtifactVersionProduced'"
                ),
                {"run": str(producer.run_id)},
            )
            == 1
        )


@pytest.mark.asyncio
async def test_saved_version_identity_cannot_be_rewritten():
    from sqlalchemy.exc import IntegrityError

    from app.application.services.artifact_service import ArtifactService

    _owner, session_id = await seed()
    artifact = await ArtifactService(
        uow, Objects(), upload_intents=UnitOfWorkUploadIntents(uow)
    ).write_content(session_id, None, "doc", "title", "immutable", verify_upload=False)
    async with execution_admin_session() as db:
        with pytest.raises(IntegrityError, match="immutable artifact version"):
            await db.execute(
                text(
                    "UPDATE artifact_version_provenance SET content_digest='rewritten' WHERE artifact_id=:id"
                ),
                {"id": artifact.id},
            )
        await db.rollback()


@pytest.mark.asyncio
async def test_private_tables_api_insert_only_and_kernel_scope_isolation():
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope, Principal
    from app.infrastructure.repositories.db_artifact_provenance_repository import (
        DBArtifactProvenanceRepository,
    )
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import (
        authenticated_session_factory,
        execution_kernel_database_uri,
    )

    owner, session_id = await seed()
    settings = load_deployment_settings()
    for kernel in (False, True):
        engine = create_async_engine(
            execution_kernel_database_uri() if kernel else settings.sqlalchemy_database_uri
        )
        factory = authenticated_session_factory(
            engine, signing_secret=settings.database_authorization_signing_secret
        )
        try:
            async with factory() as db:
                scope = OwnerScope.personal(owner)
                await configure_session_authorization(
                    db, AuthorizationContext.for_principal(Principal(user_id=owner), scope=scope)
                )
                for name in ("artifact_production_receipts", "artifact_upload_intents"):
                    assert (
                        await db.scalar(
                            text("SELECT has_table_privilege(current_user,:name,'SELECT')"),
                            {"name": name},
                        )
                        == kernel
                    )
                    assert await db.scalar(
                        text("SELECT has_table_privilege(current_user,:name,'INSERT')"),
                        {"name": name},
                    )
                    assert not await db.scalar(
                        text("SELECT has_table_privilege(current_user,:name,'DELETE')"),
                        {"name": name},
                    )
                upload, artifact = uuid4(), str(uuid4())
                await DBArtifactProvenanceRepository(db).register_upload(
                    scope,
                    upload_id=upload,
                    session_id=session_id,
                    artifact_id=artifact,
                    storage_key=f"artifacts/{session_id}/{artifact}/uploads/{upload}",
                )
                await db.commit()
                await configure_session_authorization(
                    db, AuthorizationContext.for_principal(Principal(user_id="foreign-f05"))
                )
                if kernel:
                    assert (
                        await db.scalar(
                            text(
                                "SELECT count(*) FROM artifact_upload_intents WHERE upload_id=:id"
                            ),
                            {"id": upload},
                        )
                        == 0
                    )
                else:
                    with pytest.raises(DBAPIError):
                        await db.execute(text("SELECT * FROM artifact_upload_intents LIMIT 1"))
                await db.rollback()
                await configure_session_authorization(
                    db, AuthorizationContext.for_principal(Principal(user_id="foreign-f05"))
                )
                upload = uuid4()
                with pytest.raises(DBAPIError):
                    await DBArtifactProvenanceRepository(db).register_upload(
                        OwnerScope.personal("foreign-f05"),
                        upload_id=upload,
                        session_id=session_id,
                        artifact_id=artifact,
                        storage_key=f"artifacts/{session_id}/{artifact}/uploads/{upload}",
                    )
                await db.rollback()
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_cleanup_skips_live_writer_lock_then_recovers_crashed_intent():
    from datetime import UTC, datetime, timedelta

    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )
    from app.infrastructure.repositories.db_artifact_provenance_repository import (
        DBArtifactProvenanceRepository,
    )

    owner, session_id = await seed()
    objects = Objects()
    upload, artifact = uuid4(), str(uuid4())
    key = f"artifacts/{session_id}/{artifact}/uploads/{upload}"
    maintenance = ArtifactProvenanceMaintenance(
        session_factory=execution_admin_session,
        authorization=AuthorizationContext.system("f05"),
        objects=objects,
        handler=None,
    )
    async with execution_admin_session() as writer:
        await DBArtifactProvenanceRepository(writer).lock_upload(upload)
        async with uow() as unit:
            await unit.artifact_provenance.register_upload(
                OwnerScope.personal(owner),
                upload_id=upload,
                session_id=session_id,
                artifact_id=artifact,
                storage_key=key,
            )
            await unit.commit()
        await objects.put_bytes(key, b"upload-in-flight")
        await maintenance.cleanup_uploads(
            before=datetime.now(UTC) + timedelta(seconds=1), limit=1000
        )
        assert objects.data[key] == b"upload-in-flight"
        await writer.rollback()  # models crashed DB transaction; durable intent remains
    await maintenance.cleanup_uploads(before=datetime.now(UTC) + timedelta(seconds=1), limit=1000)
    assert key not in objects.data


@pytest.mark.asyncio
async def test_replayed_write_returns_its_original_version_after_other_writes():
    from app.application.services.artifact_service import ArtifactService

    session_id, producer, _command, _handler = await production_run()
    service = ArtifactService(uow, Objects(), upload_intents=UnitOfWorkUploadIntents(uow))
    first = await service.write_content(
        session_id, None, "doc", "first", "original", producer=producer, verify_upload=False
    )
    await service.write_content(session_id, first.id, "doc", "second", "newer", verify_upload=False)
    repeated = await service.write_content(
        session_id, None, "doc", "first", "original", producer=producer, verify_upload=False
    )
    assert repeated.storage_ref == first.storage_ref
    assert repeated.version_refs == first.version_refs


@pytest.mark.asyncio
async def test_one_version_retains_multiple_exact_producer_associations():
    from app.application.services.artifact_service import ArtifactService
    from app.domain.models.artifact_provenance import ArtifactVersionProvenance
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector

    session_id, producer, command, handler = await production_run()
    objects = Objects()
    first = await ArtifactService(
        uow, objects, upload_intents=UnitOfWorkUploadIntents(uow)
    ).write_content(
        session_id, None, "doc", "title", "shared", producer=producer, verify_upload=False
    )
    second = producer.model_copy(update={"claim_generation": 2})
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE execution_activity_tasks SET claim_generation=2 WHERE activity_id=:id"),
            {"id": producer.activity_id},
        )
        await db.commit()
    await command(
        "MarkActivityCallStarted",
        {"activity_id": str(producer.activity_id), "generation": 0, "claim_generation": 2},
        2,
    )
    async with uow() as unit:
        original = (await unit.artifact_provenance.get_version(producer.scope, first.id, 1))[0]
        await unit.artifact_provenance.record_version(
            producer.scope,
            ArtifactVersionProvenance(
                id=uuid4(),
                artifact_id=first.id,
                version=1,
                producer_identity=str(second.operation_id),
                evidence_kind="derived",
                binding_status="pending",
                content_digest=original.content_digest,
            ),
            producer=second,
            storage_key=first.storage_ref,
        )
        await unit.commit()
    maintenance = ArtifactProvenanceMaintenance(
        session_factory=execution_admin_session,
        authorization=AuthorizationContext.system("f05"),
        objects=objects,
        handler=handler,
    )
    await maintenance.process_pending()
    await PostgresFormalProjector(
        session_factory=kernel_session, authorization=AuthorizationContext.system("f05")
    ).run_once(producer.scope, limit=1000)
    async with uow() as unit:
        records = await unit.artifact_provenance.get_version(producer.scope, first.id, 1)
        assert len(records) == 2
        assert {r.binding_status for r in records} == {"bound"}
        assert len({r.attempt_id for r in records}) == 2
        assert {r.evidence_kind for r in records} == {"direct", "derived"}


@pytest.mark.parametrize("experimental_null", [False, True])
@pytest.mark.asyncio
async def test_raw_legacy_production_shapes_upcast_after_hash_check(experimental_null):
    from app.application.services.artifact_service import ArtifactService
    from app.domain.execution.run import RunAggregate
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )
    from app.infrastructure.execution.postgres_event_store import PostgresEventStore
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector

    session_id, producer, _command, handler = await production_run()
    objects = Objects()
    artifact = await ArtifactService(
        uow, objects, upload_intents=UnitOfWorkUploadIntents(uow)
    ).write_content(
        session_id, None, "doc", "legacy", "bytes", producer=producer, verify_upload=False
    )

    class HistoricalFixtureStore(PostgresEventStore):
        async def append(self, stream, expected_version, events, context):
            old = []
            for event in events:
                if event.event_type == "ArtifactVersionProduced":
                    internal = dict(event.internal_payload)
                    if not experimental_null:
                        internal.pop("invocation_id")
                    event = event.model_copy(
                        update={"event_schema_version": 1, "internal_payload": internal}
                    )
                old.append(event)
            return await super().append(stream, expected_version, tuple(old), context)

    handler._event_store_factory = lambda session: HistoricalFixtureStore(
        session, event_registries={"run": RunAggregate().event_registry}
    )
    await ArtifactProvenanceMaintenance(
        session_factory=execution_admin_session,
        authorization=AuthorizationContext.system("f05"),
        objects=objects,
        handler=handler,
    ).process_pending()
    async with execution_admin_session() as db:
        raw = (
            await db.execute(
                text(
                    "SELECT event_schema_version,internal_payload,event_hash FROM execution_events WHERE stream_id=:run AND event_type='ArtifactVersionProduced'"
                ),
                {"run": str(producer.run_id)},
            )
        ).one()
        assert raw.event_schema_version == 1
        assert ("invocation_id" in raw.internal_payload) == experimental_null
        loaded = await PostgresEventStore(
            db, event_registries={"run": RunAggregate().event_registry}
        ).load_stream("run", str(producer.run_id))
        event = loaded[-1]
        assert event.event_schema_version == 2
        assert event.internal_payload["invocation_id"] is None
        assert event.event_hash == raw.event_hash
        unchanged = (
            await db.execute(
                text("SELECT internal_payload,event_hash FROM execution_events WHERE event_id=:id"),
                {"id": event.event_id},
            )
        ).one()
        assert unchanged.internal_payload == raw.internal_payload
        assert unchanged.event_hash == raw.event_hash
    await PostgresFormalProjector(
        session_factory=kernel_session, authorization=AuthorizationContext.system("f05")
    ).run_once(producer.scope, limit=1000)
    async with uow() as unit:
        assert (await unit.artifact_provenance.get_version(producer.scope, artifact.id, 1))[
            0
        ].binding_status == "bound"
