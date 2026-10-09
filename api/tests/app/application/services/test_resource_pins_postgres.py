import pytest
from sqlalchemy import text

from tests.app.application.services.test_artifact_provenance_postgres import seed
from tests.app.artifact_test_support import UnitOfWorkUploadIntents
from tests.app.execution_test_support import execution_admin_session

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("postgres_integration")]


async def test_pin_atomicity_scoped_identity_and_repository_cascade_guard():
    owner, session_id = await seed()
    async with execution_admin_session() as db:
        assert await db.scalar(text("SELECT to_regclass('public.resource_pins')")), (
            "resource pin schema missing"
        )
    from app.domain.models.resource_pin import ResourceIdentity, ResourcePinned
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository
    from app.infrastructure.repositories.db_session_repository import DBSessionRepository

    scope = OwnerScope.personal(owner)
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "INSERT INTO artifacts(id,session_id,kind,title,version_refs) VALUES ('pin-'||:session,:session,'doc','pinned','[\"fixed-key\"]')"
            ),
            {"session": session_id},
        )
        await db.commit()
    ref = ResourceIdentity(
        resource_kind="artifact", resource_id="pin-" + session_id, resource_version="1"
    )
    async with execution_admin_session() as db:
        repo = DBResourcePinRepository(db)
        await repo.acquire(scope, "session", session_id, [ref, ref])
        assert all(x.available for x in await repo.validate(scope, "session", session_id, [ref]))
        assert (
            await db.scalar(
                text("SELECT count(*) FROM resource_pins WHERE owner_id=:id"), {"id": session_id}
            )
            == 1
        )
        await db.commit()
    async with execution_admin_session() as db:
        with pytest.raises(ResourcePinned):
            await DBSessionRepository(db).purge(session_id, scope=scope)
    from sqlalchemy.exc import DBAPIError

    async with execution_admin_session() as db:
        with pytest.raises(DBAPIError, match="resource is pinned"):
            await db.execute(text("DELETE FROM sessions WHERE id=:id"), {"id": session_id})
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE sessions SET deleted_at=CURRENT_TIMESTAMP WHERE id=:id"),
            {"id": session_id},
        )
        assert await DBSessionRepository(db).purge(session_id, scope=scope, force=True)
        await db.commit()
    async with execution_admin_session() as db:
        result = await DBResourcePinRepository(db).validate(scope, "session", session_id, [ref])
        assert len(result) == 1
        assert not result[0].available
        assert result[0].reason == "force_deleted"


async def test_knowledge_gc_excludes_pin_and_release_allows_collection():
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from app.domain.models.resource_pin import ResourceIdentity
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.repositories.db_knowledge_version_repository import (
        DBKnowledgeVersionRepository,
    )
    from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository

    owner, session_id = await seed()
    scope = OwnerScope.personal(owner)
    kb, version = uuid4().hex, uuid4().hex
    async with execution_admin_session() as db:
        await db.execute(
            text("INSERT INTO knowledge_bases(id,owner_user_id) VALUES (:id,:owner)"),
            {"id": kb, "owner": owner},
        )
        await db.execute(
            text(
                "INSERT INTO knowledge_base_versions(id,knowledge_base_id,state,published_at,created_at) VALUES (:id,:kb,'ready',CURRENT_TIMESTAMP-INTERVAL '100 days',CURRENT_TIMESTAMP-INTERVAL '100 days')"
            ),
            {"id": version, "kb": kb},
        )
        await db.commit()
    ref = ResourceIdentity(resource_kind="knowledge_base", resource_id=kb, resource_version=version)
    async with execution_admin_session() as db:
        await DBResourcePinRepository(db).acquire(scope, "session", session_id, [ref])
        await db.commit()
    async with execution_admin_session() as db:
        gc = DBKnowledgeVersionRepository(db)
        result = await gc.collect_garbage(
            retain_count=0, older_than=datetime.now(UTC) - timedelta(days=30), batch_size=500
        )
        assert version not in result.collected_version_ids
        assert result.protected_pinned_versions >= 1
        await db.rollback()
    async with execution_admin_session() as db:
        await DBResourcePinRepository(db).release(scope, "session", session_id, [ref])
        gc = DBKnowledgeVersionRepository(db)
        result = await gc.collect_garbage(
            retain_count=0, older_than=datetime.now(UTC) - timedelta(days=30), batch_size=500
        )
        assert version in result.collected_version_ids
        await db.rollback()


async def test_pin_commit_between_gc_candidates_and_resource_lock_is_rechecked():
    import asyncio
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from app.domain.models.resource_pin import ResourceIdentity
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.repositories.db_knowledge_version_repository import (
        DBKnowledgeVersionRepository,
    )
    from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository

    owner, session_id = await seed()
    scope = OwnerScope.personal(owner)
    kb, version = uuid4().hex, uuid4().hex
    async with execution_admin_session() as db:
        await db.execute(
            text("INSERT INTO knowledge_bases(id,owner_user_id) VALUES (:id,:owner)"),
            {"id": kb, "owner": owner},
        )
        await db.execute(
            text(
                "INSERT INTO knowledge_base_versions(id,knowledge_base_id,state,published_at,created_at) VALUES (:id,:kb,'ready',CURRENT_TIMESTAMP-INTERVAL '100 days',CURRENT_TIMESTAMP-INTERVAL '100 days')"
            ),
            {"id": version, "kb": kb},
        )
        await db.commit()
    ref = ResourceIdentity(resource_kind="knowledge_base", resource_id=kb, resource_version=version)
    ready = asyncio.Event()
    gc_pid = []

    async def gc():
        async with execution_admin_session() as db:
            gc_pid.append(await db.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            result = await DBKnowledgeVersionRepository(db).collect_garbage(
                retain_count=0, older_than=datetime.now(UTC) - timedelta(days=30), batch_size=500
            )
            await db.rollback()
            return result

    async with execution_admin_session() as pin_db:
        await DBResourcePinRepository(pin_db).acquire(scope, "session", session_id, [ref])
        blocker = await pin_db.scalar(text("SELECT pg_backend_pid()"))
        task = asyncio.create_task(gc())
        try:
            await ready.wait()
            async with execution_admin_session() as observer:
                async with asyncio.timeout(5):
                    while not await observer.scalar(  # noqa: ASYNC110 - observe a PostgreSQL lock, not a local event
                        text("SELECT :blocker=ANY(pg_blocking_pids(:pid))"),
                        {"blocker": blocker, "pid": gc_pid[0]},
                    ):
                        await asyncio.sleep(0.01)
            await pin_db.commit()
            result = await asyncio.wait_for(task, 5)
            assert version not in result.collected_version_ids
        finally:
            if not task.done():
                task.cancel()


async def test_artifact_pin_preserves_actual_upload_until_force_session_purge():
    from datetime import UTC, datetime, timedelta

    from app.application.services.artifact_service import ArtifactService
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.resource_pin import ResourceIdentity, ResourcePinned
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )
    from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository
    from app.infrastructure.repositories.db_session_repository import DBSessionRepository
    from tests.app.application.services.test_artifact_provenance_postgres import Objects, uow

    class RetryObjects(Objects):
        fail = False

        async def delete_bytes(self, key):
            if self.fail and key == self.failed_key:
                raise OSError("temporary storage failure")
            await super().delete_bytes(key)

    owner, session_id = await seed()
    scope = OwnerScope.personal(owner)
    objects = RetryObjects()
    artifact = await ArtifactService(
        uow, objects, upload_intents=UnitOfWorkUploadIntents(uow)
    ).write_content(session_id, None, "doc", "pin", "retained", verify_upload=False)
    second = await ArtifactService(
        uow, objects, upload_intents=UnitOfWorkUploadIntents(uow)
    ).write_content(session_id, None, "doc", "second", "progress", verify_upload=False)
    objects.failed_key = artifact.version_refs[0]
    ref = ResourceIdentity(resource_kind="artifact", resource_id=artifact.id, resource_version="1")
    async with execution_admin_session() as db:
        await DBResourcePinRepository(db).acquire(scope, "session", session_id, [ref])
        await db.execute(
            text("UPDATE sessions SET deleted_at=CURRENT_TIMESTAMP WHERE id=:id"),
            {"id": session_id},
        )
        await db.commit()
    maintenance = ArtifactProvenanceMaintenance(
        session_factory=execution_admin_session,
        authorization=AuthorizationContext.system("f06"),
        objects=objects,
        handler=None,
    )
    async with execution_admin_session() as db:
        with pytest.raises(ResourcePinned):
            await DBSessionRepository(db).purge(session_id, scope=scope)
    await maintenance.cleanup_uploads(before=datetime.now(UTC) + timedelta(days=1))
    assert objects.data[artifact.version_refs[0]] == b"retained"
    async with execution_admin_session() as db:
        assert await DBSessionRepository(db).purge(session_id, scope=scope, force=True)
        await db.commit()
    objects.fail = True
    await maintenance.cleanup_uploads(before=datetime.now(UTC) + timedelta(days=1))
    assert artifact.version_refs[0] in objects.data
    assert second.version_refs[0] not in objects.data
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM artifact_retired_objects WHERE artifact_id=:id AND cleaned_at IS NULL"
                ),
                {"id": artifact.id},
            )
            == 1
        )
        assert not (
            await DBResourcePinRepository(db).validate(scope, "session", session_id, [ref])
        )[0].available
    objects.fail = False
    await maintenance.cleanup_uploads(before=datetime.now(UTC) + timedelta(days=1))
    assert artifact.version_refs[0] not in objects.data
    async with execution_admin_session() as db:
        assert not (
            await DBResourcePinRepository(db).validate(scope, "session", session_id, [ref])
        )[0].available


async def test_pin_batch_failure_rolls_back_all_and_broad_owner_delete_is_blocked():
    from uuid import uuid4

    from sqlalchemy.exc import DBAPIError

    from app.domain.models.resource_pin import ResourceIdentity, ResourceUnavailable
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository
    from app.infrastructure.repositories.db_user_repository import DBUserRepository

    owner, session_id = await seed()
    scope = OwnerScope.personal(owner)
    kb, version = uuid4().hex, uuid4().hex
    async with execution_admin_session() as db:
        await db.execute(
            text("INSERT INTO knowledge_bases(id,owner_user_id) VALUES (:id,:owner)"),
            {"id": kb, "owner": owner},
        )
        await db.execute(
            text(
                "INSERT INTO knowledge_base_versions(id,knowledge_base_id,state,published_at) VALUES (:id,:kb,'ready',CURRENT_TIMESTAMP)"
            ),
            {"id": version, "kb": kb},
        )
        await db.commit()
    valid = ResourceIdentity(
        resource_kind="knowledge_base", resource_id=kb, resource_version=version
    )
    absent = ResourceIdentity(
        resource_kind="knowledge_base", resource_id="zz-missing", resource_version="missing"
    )
    async with execution_admin_session() as db:
        repo = DBResourcePinRepository(db)
        with pytest.raises(ResourceUnavailable):
            await repo.acquire(scope, "session", session_id, [valid, absent])
        assert (
            await db.scalar(
                text("SELECT count(*) FROM resource_pins WHERE owner_id=:id"), {"id": session_id}
            )
            == 0
        )
        await repo.acquire(scope, "session", session_id, [valid])
        await db.commit()
    async with execution_admin_session() as db:
        with pytest.raises(DBAPIError, match="resource is pinned"):
            await DBUserRepository(db).delete_owned_resources(owner)


async def test_api_delete_derives_retirement_authority_but_cannot_forge_queue():
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope, Principal
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.infrastructure.security.test_execution_view_rls import factory

    owner, session_id = await seed()
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "INSERT INTO artifacts(id,session_id,kind,title,version_refs) VALUES (:id,:id,'doc','retire','[\"exact-object\"]')"
            ),
            {"id": session_id},
        )
        await db.commit()
    engine = create_async_engine(load_deployment_settings().sqlalchemy_database_uri)
    try:
        async with factory(engine)() as db:
            await configure_session_authorization(
                db,
                AuthorizationContext.for_principal(
                    Principal(user_id=owner), scope=OwnerScope.personal(owner)
                ),
            )
            await db.execute(text("DELETE FROM sessions WHERE id=:id"), {"id": session_id})
            await db.commit()
        async with execution_admin_session() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT storage_key,owner_user_id FROM artifact_retired_objects WHERE artifact_id=:id"
                    ),
                    {"id": session_id},
                )
            ).all()
            assert rows == [("exact-object", owner)]
        async with factory(engine)() as db:
            await configure_session_authorization(
                db,
                AuthorizationContext.for_principal(
                    Principal(user_id=owner), scope=OwnerScope.personal(owner)
                ),
            )
            with pytest.raises(DBAPIError, match="permission denied"):
                await db.execute(
                    text(
                        "INSERT INTO artifact_retired_objects(retirement_id,artifact_id,session_id,storage_key,owner_user_id,created_by) VALUES (gen_random_uuid(),'forged','forged','arbitrary',:owner,:owner)"
                    ),
                    {"owner": owner},
                )
    finally:
        await engine.dispose()


async def test_team_repository_cascade_cannot_bypass_available_pin():
    from uuid import uuid4

    from sqlalchemy.exc import DBAPIError

    from app.domain.models.resource_pin import ResourceIdentity, ResourcePinned
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository
    from app.infrastructure.repositories.db_team_repository import DBTeamRepository

    owner, session_id = await seed()
    team, kb, version = (uuid4().hex for _ in range(3))
    scope = OwnerScope.team(owner, team)
    async with execution_admin_session() as db:
        await db.execute(
            text("INSERT INTO teams(id,name,created_by) VALUES (:id,:id,:owner)"),
            {"id": team, "owner": owner},
        )
        await db.execute(
            text("UPDATE sessions SET team_id=:team WHERE id=:id"),
            {"id": session_id, "team": team},
        )
        await db.execute(
            text("INSERT INTO knowledge_bases(id,team_id) VALUES (:id,:team)"),
            {"id": kb, "team": team},
        )
        await db.execute(
            text(
                "INSERT INTO knowledge_base_versions(id,knowledge_base_id,state,published_at) VALUES (:id,:kb,'ready',CURRENT_TIMESTAMP)"
            ),
            {"id": version, "kb": kb},
        )
        await DBResourcePinRepository(db).acquire(
            scope,
            "session",
            session_id,
            [
                ResourceIdentity(
                    resource_kind="knowledge_base", resource_id=kb, resource_version=version
                )
            ],
        )
        await db.commit()
    async with execution_admin_session() as db:
        with pytest.raises(DBAPIError, match="resource is pinned"):
            await DBTeamRepository(db).delete_resources(team)
    async with execution_admin_session() as db:
        with pytest.raises(ResourcePinned) as retained:
            await DBTeamRepository(db).delete_by_id(team)
        assert retained.value.resource_kind == "team"
        assert retained.value.resource_id == team
