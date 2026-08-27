import asyncio
import json
from importlib import import_module
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.models.authorization import AuthorizationContext
from app.domain.runtime_policy import (
    AgentExecutionPolicy,
    ExecutionPolicy,
    OperationsPolicy,
    RuntimePolicyHeadConflictError,
    RuntimePolicyIntegrityError,
    policy_digest,
)
from app.infrastructure.security.db_authorization import (
    configure_sync_system_authorization,
)
from core.config import (
    load_deployment_settings,
    sqlalchemy_sync_migration_database_uri,
)
from tests.app.execution_test_support import authenticated_session_factory


def _seed_policy_head() -> tuple[UUID, UUID]:
    execution_id = uuid4()
    operations_id = uuid4()
    execution = ExecutionPolicy()
    operations = OperationsPolicy()
    engine = create_engine(sqlalchemy_sync_migration_database_uri(load_deployment_settings()))
    try:
        with engine.begin() as connection:
            configure_sync_system_authorization(
                connection,
                actor="runtime-policy-repository-test-seed",
                signing_secret=load_deployment_settings().session_secret,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO execution_policy_revisions
                        (id, schema_version, payload, digest, created_by, note)
                    VALUES
                        (:id, 1, CAST(:payload AS jsonb), :digest, :actor, :note)
                    """
                ),
                {
                    "id": execution_id,
                    "payload": json.dumps(execution.model_dump(mode="json")),
                    "digest": policy_digest(1, execution),
                    "actor": "admin-1",
                    "note": "repository test execution seed",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO operations_policy_revisions
                        (id, schema_version, payload, digest, created_by, note)
                    VALUES
                        (:id, 1, CAST(:payload AS jsonb), :digest, :actor, :note)
                    """
                ),
                {
                    "id": operations_id,
                    "payload": json.dumps(operations.model_dump(mode="json")),
                    "digest": policy_digest(1, operations),
                    "actor": "admin-1",
                    "note": "repository test operations seed",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO runtime_policy_heads
                        (id, version, execution_revision_id,
                         operations_revision_id, updated_by)
                    VALUES
                        ('global', 1, :execution_id, :operations_id, :actor)
                    ON CONFLICT (id) DO UPDATE SET
                        version = EXCLUDED.version,
                        execution_revision_id = EXCLUDED.execution_revision_id,
                        operations_revision_id = EXCLUDED.operations_revision_id,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = CURRENT_TIMESTAMP(0)
                    """
                ),
                {
                    "execution_id": execution_id,
                    "operations_id": operations_id,
                    "actor": "admin-1",
                },
            )
    finally:
        engine.dispose()
    return execution_id, operations_id


@pytest.fixture
def seeded_policy_head(postgres_integration) -> tuple[UUID, UUID]:
    del postgres_integration
    return _seed_policy_head()


@pytest.fixture
async def policy_repository(seeded_policy_head):
    del seeded_policy_head
    repository_type = import_module(
        "app.infrastructure.repositories.postgres_runtime_policy_repository"
    ).PostgresRuntimePolicyRepository
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    sessions = authenticated_session_factory(
        engine,
        signing_secret=settings.session_secret,
    )
    repository = repository_type(
        session_factory=sessions,
        authorization=AuthorizationContext.system("runtime-policy-repository-test"),
    )
    try:
        yield repository
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_policy_cas_has_one_winner(policy_repository) -> None:
    active = await policy_repository.load_active_pair()
    first_policy = active.execution.revision.policy.model_copy(
        update={"agent": AgentExecutionPolicy(max_iterations=20)}
    )
    second_policy = active.execution.revision.policy.model_copy(
        update={"agent": AgentExecutionPolicy(max_iterations=21)}
    )

    first, second = await asyncio.gather(
        policy_repository.create_and_activate_execution(
            policy=first_policy,
            expected_head_version=active.execution.head.version,
            expected_active_revision_id=active.execution.revision.id,
            actor="admin-1",
            note="first concurrent activation",
        ),
        policy_repository.create_and_activate_execution(
            policy=second_policy,
            expected_head_version=active.execution.head.version,
            expected_active_revision_id=active.execution.revision.id,
            actor="admin-2",
            note="second concurrent activation",
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(item, Exception) for item in (first, second)) == 1
    assert sum(isinstance(item, RuntimePolicyHeadConflictError) for item in (first, second)) == 1


@pytest.mark.asyncio
async def test_restore_creates_distinct_revision_with_same_digest(
    policy_repository,
) -> None:
    original = (await policy_repository.load_active_pair()).execution

    restored = await policy_repository.create_and_activate_execution(
        policy=original.revision.policy,
        expected_head_version=original.head.version,
        expected_active_revision_id=original.revision.id,
        actor="admin-1",
        note="restore active execution payload",
        restored_from_id=original.revision.id,
    )

    assert restored.revision.id != original.revision.id
    assert restored.revision.digest == original.revision.digest
    assert restored.revision.restored_from_id == original.revision.id


@pytest.mark.asyncio
async def test_active_read_rejects_digest_mismatch(
    policy_repository,
) -> None:
    original = await policy_repository.load_active_pair()
    corrupted_id = uuid4()
    engine = create_engine(sqlalchemy_sync_migration_database_uri(load_deployment_settings()))
    try:
        with engine.begin() as connection:
            configure_sync_system_authorization(
                connection,
                actor="runtime-policy-corruption-test",
                signing_secret=load_deployment_settings().session_secret,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO execution_policy_revisions
                        (id, schema_version, payload, digest, created_by, note)
                    VALUES
                        (:id, 1, CAST(:payload AS jsonb), :digest, :actor, :note)
                    """
                ),
                {
                    "id": corrupted_id,
                    "payload": json.dumps(
                        original.execution.revision.policy.model_dump(mode="json")
                    ),
                    "digest": "sha256:" + "0" * 64,
                    "actor": "admin-1",
                    "note": "deliberately corrupt digest",
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE runtime_policy_heads
                    SET version = version + 1,
                        execution_revision_id = :revision_id,
                        updated_by = :actor,
                        updated_at = CURRENT_TIMESTAMP(0)
                    WHERE id = 'global'
                    """
                ),
                {"revision_id": corrupted_id, "actor": "admin-1"},
            )

        with pytest.raises(RuntimePolicyIntegrityError):
            await policy_repository.load_active_pair()
    finally:
        with engine.begin() as connection:
            configure_sync_system_authorization(
                connection,
                actor="runtime-policy-corruption-test-restore",
                signing_secret=load_deployment_settings().session_secret,
            )
            connection.execute(
                text(
                    """
                    UPDATE runtime_policy_heads
                    SET version = version + 1,
                        execution_revision_id = :execution_revision_id,
                        operations_revision_id = :operations_revision_id,
                        updated_by = :actor,
                        updated_at = CURRENT_TIMESTAMP(0)
                    WHERE id = 'global'
                    """
                ),
                {
                    "execution_revision_id": original.execution.revision.id,
                    "operations_revision_id": original.operations.revision.id,
                    "actor": "admin-1",
                },
            )
        engine.dispose()
