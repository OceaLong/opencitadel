"""Production-style transactions must rebind kernel RLS claims after each item."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.application.services.artifact_service import ArtifactService
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.infrastructure.execution.postgres_artifact_provenance import ArtifactProvenanceMaintenance
from app.infrastructure.repositories.db_artifact_provenance_repository import (
    DBArtifactProvenanceRepository,
)
from app.infrastructure.repositories.db_artifact_repository import DBArtifactRepository
from app.infrastructure.repositories.db_session_repository import DBSessionRepository
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
    configure_sync_system_authorization,
)
from core.config import load_deployment_settings
from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_artifact_provenance_postgres import Objects
from tests.app.artifact_test_support import UnitOfWorkUploadIntents
from tests.app.execution_test_support import execution_kernel_database_uri

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("postgres_integration")]


@pytest.mark.parametrize("authority", ["upload", "retirement"])
@pytest.mark.parametrize("failure", ["raise", "hang"])
async def test_kernel_batch_reauthorizes_failed_first_and_successful_second(
    isolated_database,  # noqa: F811
    monkeypatch,
    authority,
    failure,
):
    import asyncio

    admin, config = isolated_database
    command.upgrade(config, "head")
    settings = load_deployment_settings()
    engine = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=admin.url.database)
    )
    # Match production: plain sessionmaker, no transaction-start claim hooks.
    sessions = async_sessionmaker(
        bind=engine,
        autoflush=False,
        info={
            "database_authorization_signing_secret": settings.database_authorization_signing_secret
        },
    )
    auth = AuthorizationContext.system("f06-real-kernel-batch")
    owner, session_id = "f06-" + uuid4().hex, str(uuid4())
    scope = OwnerScope.personal(owner)
    with admin.begin() as db:
        configure_sync_system_authorization(
            db,
            actor="f06-cleanup-fixture",
            signing_secret=settings.database_authorization_signing_secret,
        )
        db.execute(
            text("INSERT INTO users(id,email,username) VALUES (:id,:email,:id)"),
            {"id": owner, "email": owner + "@test.invalid"},
        )
        db.execute(
            text("INSERT INTO sessions(id,owner_user_id,status) VALUES (:id,:owner,'running')"),
            {"id": session_id, "owner": owner},
        )

    @asynccontextmanager
    async def uow():
        async with sessions() as db:
            await configure_session_authorization(db, auth)
            yield SimpleNamespace(
                artifact=DBArtifactRepository(db),
                artifact_provenance=DBArtifactProvenanceRepository(db),
                commit=db.commit,
            )

    class ObjectFailures(Objects):
        failed_key = None

        async def delete_bytes(self, key):
            if key == self.failed_key:
                if failure == "raise":
                    raise OSError("first unavailable")
                await asyncio.Event().wait()
            await super().delete_bytes(key)

    objects = ObjectFailures()
    try:
        async with sessions() as db:
            await configure_session_authorization(db, auth)
            assert (
                await db.scalar(
                    text("SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user")
                )
                is False
            )
            assert (
                await db.scalar(
                    text(
                        "SELECT relowner <> (SELECT oid FROM pg_roles WHERE rolname=current_user) AND relrowsecurity FROM pg_class WHERE oid='artifact_upload_intents'::regclass"
                    )
                )
                is True
            )
            assert await db.scalar(text("SELECT opencitadel_authorization_valid()")) is True
            await db.commit()
            assert await db.scalar(text("SELECT opencitadel_authorization_valid()")) is False
        artifacts = ArtifactService(uow, objects, upload_intents=UnitOfWorkUploadIntents(uow))
        first = await artifacts.write_content(
            session_id, None, "doc", "first", "failed", verify_upload=False
        )
        second = await artifacts.write_content(
            session_id, None, "doc", "second", "healthy", verify_upload=False
        )
        sentinel = datetime(2000, 1, 1, tzinfo=UTC)
        async with sessions() as db:
            await configure_session_authorization(db, auth)
            if authority == "retirement":
                await db.execute(
                    text(
                        "UPDATE artifact_upload_intents SET cleaned_at=CURRENT_TIMESTAMP WHERE session_id=:id"
                    ),
                    {"id": session_id},
                )
            await db.execute(
                text("UPDATE sessions SET deleted_at=CURRENT_TIMESTAMP WHERE id=:id"),
                {"id": session_id},
            )
            assert await DBSessionRepository(db).purge(session_id, scope=scope, force=True)
            for table, key in [
                ("artifact_upload_intents", "artifact_id"),
                ("artifact_retired_objects", "artifact_id"),
            ]:
                for artifact_id, offset in [(first.id, 0), (second.id, 1)]:
                    # Operational retry-order metadata only, never immutable identity.
                    await db.execute(
                        text(f"UPDATE {table} SET updated_at=:time WHERE {key}=:id"),
                        {"time": sentinel + timedelta(seconds=offset), "id": artifact_id},
                    )
            await db.commit()

        class Later(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(days=2)

        monkeypatch.setattr(
            "app.infrastructure.execution.postgres_artifact_provenance.datetime", Later
        )
        maintenance = ArtifactProvenanceMaintenance(
            session_factory=sessions, authorization=auth, objects=objects, handler=None
        )
        maintenance._object_delete_timeout = 0.05
        objects.failed_key = first.version_refs[0]
        await maintenance.process_pending(limit=10)
        table = "artifact_upload_intents" if authority == "upload" else "artifact_retired_objects"
        async with sessions() as db:
            await configure_session_authorization(db, auth)
            rows = (
                (
                    await db.execute(
                        text(
                            f"SELECT artifact_id,cleaned_at,updated_at FROM {table} WHERE artifact_id IN (:first,:second)"
                        ),
                        {"first": first.id, "second": second.id},
                    )
                )
                .mappings()
                .all()
            )
        indexed = {row["artifact_id"]: row for row in rows}
        assert indexed[first.id]["cleaned_at"] is None
        assert indexed[first.id]["updated_at"] > sentinel + timedelta(seconds=2)
        assert indexed[second.id]["cleaned_at"] is not None, (
            "second item lost transaction authorization"
        )
        assert first.version_refs[0] in objects.data
        assert second.version_refs[0] not in objects.data
        objects.failed_key = None
        await maintenance.process_pending(limit=10)
        async with sessions() as db:
            await configure_session_authorization(db, auth)
            assert (
                await db.scalar(
                    text(f"SELECT cleaned_at IS NOT NULL FROM {table} WHERE artifact_id=:id"),
                    {"id": first.id},
                )
                is True
            )
        assert first.version_refs[0] not in objects.data
    finally:
        await engine.dispose()
