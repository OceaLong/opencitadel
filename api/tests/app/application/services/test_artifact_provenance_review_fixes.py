import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from tests.app.application.services.test_artifact_provenance_postgres import (
    Objects,
    kernel_session,
    production_run,
    seed,
    uow,
)
from tests.app.artifact_test_support import UnitOfWorkUploadIntents
from tests.app.execution_test_support import execution_admin_session

pytestmark = pytest.mark.usefixtures("postgres_integration")


@pytest.mark.parametrize("operation", ["finalize", "create_share_link", "revoke_share_link"])
@pytest.mark.asyncio
async def test_metadata_writer_serializes_before_loading_version(operation):
    from app.application.services.artifact_service import ArtifactService

    _owner, session_id = await seed()
    objects = Objects()
    writer = ArtifactService(uow, objects, upload_intents=UnitOfWorkUploadIntents(uow))
    artifact = await writer.write_content(
        session_id, None, "doc", "title", "v1", verify_upload=False
    )
    loaded, release = asyncio.Event(), asyncio.Event()

    async def no_audit(*args, **kwargs):
        pass

    @asynccontextmanager
    async def paused_uow():
        async with uow() as unit:
            original = unit.artifact.get_by_id

            async def get(identifier):
                value = await original(identifier)
                loaded.set()
                await release.wait()
                return value

            unit.artifact.get_by_id = get
            unit.audit = SimpleNamespace(add=no_audit)
            yield unit

    metadata = ArtifactService(
        paused_uow, objects, upload_intents=UnitOfWorkUploadIntents(paused_uow)
    )
    call = getattr(metadata, operation)
    mutation = asyncio.create_task(
        call(session_id, artifact.id) if operation == "finalize" else call(artifact.id)
    )
    await loaded.wait()
    version = asyncio.create_task(
        writer.write_content(session_id, artifact.id, "doc", "new", "v2", verify_upload=False)
    )
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(version), 0.15)
    finally:
        release.set()
        await mutation
        await version
    async with uow() as unit:
        current = await unit.artifact.get_by_id(artifact.id)
        assert len(current.version_refs) == 2
    assert [objects.data[key] for key in current.version_refs] == [b"v1", b"v2"]
    from datetime import UTC, datetime, timedelta

    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )

    async with uow() as unit:
        assert (
            len(
                await unit.artifact_provenance.get_version(
                    OwnerScope.personal(_owner), artifact.id, 2
                )
            )
            == 1
        )
    await ArtifactProvenanceMaintenance(
        session_factory=execution_admin_session,
        authorization=AuthorizationContext.system("f05"),
        objects=objects,
        handler=None,
    ).cleanup_uploads(before=datetime.now(UTC) + timedelta(seconds=1), limit=1000)
    assert objects.data[current.version_refs[1]] == b"v2"


@pytest.mark.asyncio
async def test_deleted_pending_receipt_does_not_starve_other_scope_or_cleanup():
    from app.application.services.artifact_service import ArtifactService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector

    missing_session, missing, _command, handler = await production_run()
    valid_session, valid, _command, _handler = await production_run()
    objects = Objects()
    service = ArtifactService(uow, objects, upload_intents=UnitOfWorkUploadIntents(uow))
    lost = await service.write_content(
        missing_session, None, "doc", "lost", "lost", producer=missing, verify_upload=False
    )
    saved = await service.write_content(
        valid_session, None, "doc", "saved", "saved", producer=valid, verify_upload=False
    )
    async with execution_admin_session() as db:
        await db.execute(text("DELETE FROM sessions WHERE id=:id"), {"id": missing_session})
        await db.commit()
    maintenance = ArtifactProvenanceMaintenance(
        session_factory=execution_admin_session,
        authorization=AuthorizationContext.system("f05"),
        objects=objects,
        handler=handler,
    )
    from uuid import uuid4

    upload, orphan_artifact = uuid4(), str(uuid4())
    orphan = f"artifacts/{valid_session}/{orphan_artifact}/uploads/{upload}"
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "INSERT INTO artifact_upload_intents(upload_id,session_id,artifact_id,storage_key,owner_user_id,created_by,created_at) VALUES(:upload,:session,:artifact,:key,:owner,'f05-crash',CURRENT_TIMESTAMP-interval '2 hours')"
            ),
            {
                "upload": upload,
                "session": valid_session,
                "artifact": orphan_artifact,
                "key": orphan,
                "owner": valid.scope.user_id,
            },
        )
        await db.commit()
    await objects.put_bytes(orphan, b"orphan")
    result = await maintenance.process_pending()
    assert orphan not in objects.data
    assert result["unavailable"] >= 1
    await PostgresFormalProjector(
        session_factory=kernel_session, authorization=AuthorizationContext.system("f05")
    ).run_once(valid.scope, limit=1000)
    async with uow() as unit:
        assert (await unit.artifact_provenance.get_version(valid.scope, saved.id, 1))[
            0
        ].binding_status == "bound"
        row = (await unit.artifact_provenance.get_version(missing.scope, lost.id, 1))[0]
        assert row.binding_status == "unavailable"
        assert row.availability == "unavailable"
    async with execution_admin_session() as db:
        row = (
            (
                await db.execute(
                    text("SELECT * FROM artifact_production_receipts WHERE operation_id=:id"),
                    {"id": missing.operation_id},
                )
            )
            .mappings()
            .one()
        )
        assert row["reconciliation_status"] == "unavailable"
        assert row["event_id"] is None
        assert row["last_error"] == "artifact_unavailable"


@pytest.mark.asyncio
async def test_actual_api_cannot_insert_bound_fake_or_public_pending_authority():
    from uuid import uuid4

    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.application.services.artifact_service import ArtifactService
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import Principal
    from app.infrastructure.models.execution_view import ArtifactVersionProvenanceORM
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import authenticated_session_factory

    session_id, producer, _command, _handler = await production_run()
    artifact = await ArtifactService(
        uow, Objects(), upload_intents=UnitOfWorkUploadIntents(uow)
    ).write_content(
        session_id, None, "doc", "title", "body", producer=producer, verify_upload=False
    )
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    factory = authenticated_session_factory(
        engine, signing_secret=settings.database_authorization_signing_secret
    )
    try:
        for status in ("pending", "unavailable"):
            async with factory() as db:
                await configure_session_authorization(
                    db,
                    AuthorizationContext.for_principal(Principal(user_id=producer.scope.user_id)),
                )
                await db.execute(
                    ArtifactVersionProvenanceORM.__table__.insert(),
                    {
                        "id": uuid4(),
                        "artifact_id": artifact.id,
                        "version": 1,
                        "producer_identity": str(uuid4()),
                        "owner_user_id": producer.scope.user_id,
                        "created_by": "test",
                        "evidence_kind": "unknown",
                        "binding_status": status,
                        "availability": "available",
                        "revision": 0,
                    },
                )
                await db.rollback()
        for case in ("pending_authority", "fake_version", "fake_boundary", "bound"):
            async with factory() as db:
                await configure_session_authorization(
                    db,
                    AuthorizationContext.for_principal(Principal(user_id=producer.scope.user_id)),
                )
                values = {
                    "id": uuid4(),
                    "artifact_id": artifact.id,
                    "version": 42 if case == "fake_version" else 1,
                    "producer_identity": str(uuid4()),
                    "owner_user_id": producer.scope.user_id,
                    "created_by": "test",
                    "evidence_kind": "unknown",
                    "binding_status": "unavailable",
                    "availability": "available",
                    "revision": 0,
                }
                if case == "pending_authority":
                    values.update(binding_status="pending", producer_run_id=producer.run_id)
                if case == "fake_boundary":
                    values.update(boundary=999)
                if case == "bound":
                    # F01 permits an existing Run and arbitrary boundary, without production event.
                    from app.infrastructure.execution.postgres_formal_projector import (
                        PostgresFormalProjector,
                    )

                    await PostgresFormalProjector(
                        session_factory=kernel_session,
                        authorization=AuthorizationContext.system("f05"),
                    ).run_once(producer.scope, limit=1000)
                    values.update(
                        binding_status="bound", producer_run_id=producer.run_id, boundary=999
                    )
                if case == "pending_authority":
                    from app.infrastructure.execution.postgres_formal_projector import (
                        PostgresFormalProjector,
                    )

                    await PostgresFormalProjector(
                        session_factory=kernel_session,
                        authorization=AuthorizationContext.system("f05"),
                    ).run_once(producer.scope, limit=1000)
                with pytest.raises(DBAPIError):
                    await db.execute(ArtifactVersionProvenanceORM.__table__.insert(), values)
                await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_transient_receipt_failure_backoff_does_not_block_other_receipt():
    from app.application.services.artifact_service import ArtifactService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )

    first_session, first, _command, handler = await production_run()
    second_session, second, _command, _handler = await production_run()
    objects = Objects()
    service = ArtifactService(uow, objects, upload_intents=UnitOfWorkUploadIntents(uow))
    await service.write_content(
        first_session, None, "doc", "one", "one", producer=first, verify_upload=False
    )
    await service.write_content(
        second_session, None, "doc", "two", "two", producer=second, verify_upload=False
    )
    calls = []

    class TransientHandler:
        async def handle(self, command):
            calls.append(command.command_id)
            if command.command_id == first.operation_id:
                raise OSError("temporary failure")
            return await handler.handle(command)

    maintenance = ArtifactProvenanceMaintenance(
        session_factory=execution_admin_session,
        authorization=AuthorizationContext.system("f05"),
        objects=objects,
        handler=TransientHandler(),
    )
    result = await maintenance.process_pending()
    assert result["deferred"] >= 1
    assert second.operation_id in calls
    async with execution_admin_session() as db:
        row = (
            await db.execute(
                text(
                    "SELECT retry_attempts,next_attempt_at>now() AS delayed,last_error FROM artifact_production_receipts WHERE operation_id=:id"
                ),
                {"id": first.operation_id},
            )
        ).one()
        assert row.retry_attempts == 1
        assert row.delayed
        assert row.last_error == "transient_failure"
        assert (
            await db.scalar(
                text("SELECT event_id FROM artifact_production_receipts WHERE operation_id=:id"),
                {"id": second.operation_id},
            )
            is not None
        )
    await maintenance.process_pending()
    assert calls.count(first.operation_id) == 1


@pytest.mark.asyncio
async def test_api_cannot_prebind_claim_fields_and_actual_kernel_binds_exactly():
    from uuid import uuid4

    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.application.execution.view_facts import attempt_key
    from app.application.services.artifact_service import ArtifactService
    from app.domain.execution.run import RunAggregate
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import Principal
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
    from app.infrastructure.models.execution_view import ArtifactVersionProvenanceORM
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import (
        authenticated_session_factory,
        execution_kernel_database_uri,
    )

    session_id, producer, command, _handler = await production_run()
    objects = Objects()
    artifact = await ArtifactService(
        uow, objects, upload_intents=UnitOfWorkUploadIntents(uow)
    ).write_content(
        session_id, None, "doc", "title", "bytes", producer=producer, verify_upload=False
    )
    # A second persisted claim for the same activity makes wrong-claim references valid F01 identities.
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
    settings = load_deployment_settings()
    api_engine = create_async_engine(settings.sqlalchemy_database_uri)
    kernel_engine = create_async_engine(execution_kernel_database_uri())
    api_factory = authenticated_session_factory(
        api_engine, signing_secret=settings.database_authorization_signing_secret
    )
    kernel_factory = authenticated_session_factory(
        kernel_engine, signing_secret=settings.database_authorization_signing_secret
    )
    auth = AuthorizationContext.system("f05-kernel")
    projector = PostgresFormalProjector(session_factory=kernel_factory, authorization=auth)
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=kernel_factory, authorization=auth, aggregates={"run": RunAggregate()}
    )
    try:
        await projector.run_once(producer.scope, limit=1000)
        await ArtifactProvenanceMaintenance(
            session_factory=kernel_factory, authorization=auth, objects=objects, handler=handler
        ).process_pending()
        async with execution_admin_session() as db:
            receipt = (
                (
                    await db.execute(
                        text("SELECT * FROM artifact_production_receipts WHERE operation_id=:id"),
                        {"id": producer.operation_id},
                    )
                )
                .mappings()
                .one()
            )
        step = attempt_key(str(producer.activity_id), 0, 1)
        wrong = attempt_key(str(producer.activity_id), 0, 2)
        base = {
            "producer_run_id": producer.run_id,
            "activity_id": producer.activity_id,
            "attempt_id": step,
            "producer_step_ids": [step],
            "invocation_id": None,
            "produced_event_id": receipt["event_id"],
            "boundary": receipt["event_position"],
            "binding_status": "bound",
        }
        for change in (
            {},
            {"attempt_id": wrong},
            {"producer_step_ids": [wrong]},
            {"invocation_id": uuid4()},
        ):
            async with api_factory() as db:
                await configure_session_authorization(
                    db,
                    AuthorizationContext.for_principal(Principal(user_id=producer.scope.user_id)),
                )
                with pytest.raises(DBAPIError):
                    await db.execute(
                        ArtifactVersionProvenanceORM.__table__.update()
                        .where(ArtifactVersionProvenanceORM.id == receipt["association_id"])
                        .values(**{**base, **change})
                    )
                await db.rollback()
        # A kernel-only poisoned prebinding is still rejected by the exact
        # binder on replay; it cannot silently accept matching event_id alone.
        from app.domain.execution.aggregate import replay
        from app.infrastructure.execution.postgres_event_store import PostgresEventStore
        from app.infrastructure.execution.postgres_view_observations import observe_formal

        async with kernel_factory() as db:
            await configure_session_authorization(db, auth)
            await db.execute(
                ArtifactVersionProvenanceORM.__table__.update()
                .where(ArtifactVersionProvenanceORM.id == receipt["association_id"])
                .values(**{**base, "attempt_id": wrong, "producer_step_ids": [wrong]})
            )
            events = await PostgresEventStore(
                db, event_registries={"run": RunAggregate().event_registry}
            ).load_stream("run", str(producer.run_id))
            state = replay(RunAggregate(), events, stream_id=str(producer.run_id)).state
            with pytest.raises(ValueError, match="different authority"):
                await observe_formal(db, events[-1], state)
            await db.rollback()
        await projector.run_once(producer.scope, limit=1000)
        async with uow() as unit:
            row = (await unit.artifact_provenance.get_version(producer.scope, artifact.id, 1))[0]
            assert row.binding_status == "bound"
            assert row.attempt_id == step
            assert row.producer_step_ids == (step,)
    finally:
        await api_engine.dispose()
        await kernel_engine.dispose()


@pytest.mark.asyncio
async def test_cleanup_still_runs_on_scan_failure_without_masking_original():
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )

    @asynccontextmanager
    async def failed_sessions():
        raise OSError("receipt scan failed")
        yield

    calls = []

    class FailedMaintenance(ArtifactProvenanceMaintenance):
        async def cleanup_uploads(self, *, limit=100, before=None):
            calls.append(limit)
            raise ValueError("cleanup failed")

    maintenance = FailedMaintenance(
        session_factory=failed_sessions,
        authorization=AuthorizationContext.system("f05"),
        objects=Objects(),
        handler=None,
    )
    with pytest.raises(OSError, match="receipt scan failed"):
        await maintenance.process_pending(limit=7)
    assert calls == [7]


@pytest.mark.parametrize("lock_phase", ["initial_probe", "rejected_followup"])
@pytest.mark.asyncio
async def test_held_receipt_lock_cannot_block_other_scope_or_cleanup(lock_phase):
    from uuid import uuid4

    from app.application.services.artifact_service import ArtifactService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )

    first_session, first, _command, handler = await production_run()
    second_session, second, _command, _handler = await production_run()
    objects = Objects()
    service = ArtifactService(uow, objects, upload_intents=UnitOfWorkUploadIntents(uow))
    for session_id, producer in ((first_session, first), (second_session, second)):
        await service.write_content(
            session_id, None, "doc", "title", "bytes", producer=producer, verify_upload=False
        )
    upload, artifact = uuid4(), str(uuid4())
    orphan = f"artifacts/{second_session}/{artifact}/uploads/{upload}"
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "INSERT INTO artifact_upload_intents(upload_id,session_id,artifact_id,storage_key,owner_user_id,created_by,created_at) VALUES(:upload,:session,:artifact,:key,:owner,'f05-fix2',CURRENT_TIMESTAMP-interval '2 hours')"
            ),
            {
                "upload": upload,
                "session": second_session,
                "artifact": artifact,
                "key": orphan,
                "owner": second.scope.user_id,
            },
        )
        await db.commit()
    await objects.put_bytes(orphan, b"orphan")
    async with execution_admin_session() as holder:

        async def hold_first():
            await holder.execute(
                text(
                    "SELECT operation_id FROM artifact_production_receipts WHERE operation_id=:id FOR UPDATE"
                ),
                {"id": first.operation_id},
            )

        class RejectThenContend:
            async def handle(self, command):
                if command.command_id == first.operation_id:
                    await hold_first()
                    return SimpleNamespace(status="rejected")
                return await handler.handle(command)

        if lock_phase == "initial_probe":
            await hold_first()
        maintenance = ArtifactProvenanceMaintenance(
            session_factory=execution_admin_session,
            authorization=AuthorizationContext.system("f05"),
            objects=objects,
            handler=RejectThenContend(),
        )
        try:
            # Keep the real PostgreSQL row lock held through both processing and
            # recovery deadlines. Progress must occur BEFORE holder rollback.
            result = await asyncio.wait_for(maintenance.process_pending(), timeout=14)
            assert result["deferred"] >= 1
            assert orphan not in objects.data
            async with execution_admin_session() as db:
                assert (
                    await db.scalar(
                        text(
                            "SELECT event_id FROM artifact_production_receipts WHERE operation_id=:id"
                        ),
                        {"id": second.operation_id},
                    )
                    is not None
                )
                retained = (
                    await db.execute(
                        text(
                            "SELECT event_id,reconciliation_status,retry_attempts FROM artifact_production_receipts WHERE operation_id=:id"
                        ),
                        {"id": first.operation_id},
                    )
                ).one()
                assert retained.event_id is None
                assert retained.reconciliation_status == "pending"
                assert retained.retry_attempts == 0
        finally:
            await holder.rollback()
