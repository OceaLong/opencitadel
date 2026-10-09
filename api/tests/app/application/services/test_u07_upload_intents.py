"""Real DBUnitOfWork and durable upload recovery on an invocation-owned database."""

import asyncio

import pytest
from sqlalchemy import text

from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_artifact_provenance_postgres import Objects, seed
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_real_uow_upload_success_and_failure_intent_survives(isolated_database):  # noqa: F811
    from app.application.services.artifact_service import ArtifactService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_uow import DBUnitOfWork
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher
    from core.config import load_deployment_settings

    admin, _ = isolated_database
    assert admin.url.database.startswith("test_execution_view_")
    _, session = await seed()
    from datetime import UTC, datetime, timedelta

    from app.application.security.authorization_context import authorization_scope
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )
    from app.infrastructure.repositories.postgres_artifact_upload_intents import (
        PostgresArtifactUploadIntentWriter,
    )
    from app.infrastructure.storage.postgres import Postgres
    from tests.app.execution_test_support import execution_admin_session

    settings = load_deployment_settings()
    resource = Postgres(
        settings.model_copy(
            update={
                "sqlalchemy_database_uri": admin.url.set(
                    drivername="postgresql+asyncpg"
                ).render_as_string(hide_password=False),
                "postgres_pool_size": 1,
                "postgres_max_overflow": 0,
                "env": "test",
            }
        )
    )
    await resource.init()
    factory = resource.session_factory
    intents = PostgresArtifactUploadIntentWriter(
        resource.upload_intent_session_factory,
        signing_secret=settings.database_authorization_signing_secret,
    )

    def uow():
        return DBUnitOfWork(
            factory,
            secret_cipher=ApiKeyCipher("test-upload-intent"),
            audit_signing_key="test",
            audit_signing_key_id="test",
            database_authorization_signing_secret=settings.database_authorization_signing_secret,
            authorization_context=AuthorizationContext.system("artifact-upload-test"),
        )

    try:
        with authorization_scope(AuthorizationContext.system("artifact-upload-test")):
            objects = Objects()
            service = ArtifactService(uow, objects, upload_intents=intents)
            artifact = await asyncio.wait_for(
                service.write_content(session, None, "doc", "title", "body", verify_upload=False), 5
            )
            assert artifact.id
            entered, release = asyncio.Event(), asyncio.Event()

            class FailingObjects(Objects):
                async def put_bytes(self, key, data):
                    self.data[key] = data
                    entered.set()
                    await release.wait()
                    raise OSError("simulated upload interruption")

            failing = FailingObjects()
            failed_service = ArtifactService(uow, failing, upload_intents=intents)
            writer = asyncio.create_task(
                failed_service.write_content(
                    session, None, "doc", "failed", "partial", verify_upload=False
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), 5)
                maintenance = ArtifactProvenanceMaintenance(
                    session_factory=execution_admin_session,
                    authorization=AuthorizationContext.system("artifact-upload-test"),
                    objects=failing,
                    handler=None,
                )
                assert (
                    await maintenance.cleanup_uploads(
                        before=datetime.now(UTC) + timedelta(seconds=1), limit=100
                    )
                    == 0
                )
                assert failing.data
            finally:
                release.set()
            with pytest.raises(OSError, match="simulated"):
                await writer
            async with execution_admin_session() as db:
                assert (
                    await db.scalar(
                        text(
                            "SELECT count(*) FROM artifact_upload_intents WHERE storage_key=:key AND cleaned_at IS NULL"
                        ),
                        {"key": next(iter(failing.data))},
                    )
                    == 1
                )
                assert await db.scalar(text("SELECT count(*) FROM artifacts")) == 1
            assert (
                await maintenance.cleanup_uploads(
                    before=datetime.now(UTC) + timedelta(seconds=1), limit=100
                )
                == 1
            )
            assert not failing.data
    finally:
        await resource.shutdown()
        assert resource._upload_intent_engine is None
        assert resource._upload_intent_session_factory is None


async def test_existing_provenance_replay_and_concurrent_versions():
    from tests.app.application.services.test_artifact_provenance_postgres import (
        test_concurrent_writes_preserve_versions_and_unknown_producers,
        test_replayed_write_returns_its_original_version_after_other_writes,
    )

    await test_concurrent_writes_preserve_versions_and_unknown_producers()
    await test_replayed_write_returns_its_original_version_after_other_writes()
