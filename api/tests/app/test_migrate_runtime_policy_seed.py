import asyncio
from importlib import import_module

import pytest
from sqlalchemy import create_engine, text

from app.domain.runtime_policy import OperationsPolicy, initial_execution_policy, policy_digest
from app.infrastructure.security.db_authorization import (
    configure_sync_system_authorization,
)
from core.config import (
    DeploymentSettings,
    load_deployment_settings,
    sqlalchemy_sync_migration_database_uri,
)


@pytest.mark.asyncio
async def test_seed_session_factory_receives_database_authorization_secret(monkeypatch) -> None:
    module = import_module("app.migrate_runtime_policy_seed")
    captured: dict[str, object] = {}

    class FakeEngine:
        async def dispose(self) -> None:
            captured["disposed"] = True

    class FakeRepository:
        def __init__(self, *, session_factory, authorization) -> None:
            captured["session_factory"] = session_factory
            captured["authorization"] = authorization

        async def seed_if_missing(self, **_kwargs) -> bool:
            captured["initial_policy"] = _kwargs["execution_policy"]
            return True

    def fake_sessionmaker(_engine, **kwargs):
        captured["session_options"] = kwargs
        return object()

    monkeypatch.setattr(module, "create_async_engine", lambda *_args, **_kwargs: FakeEngine())
    monkeypatch.setattr(module, "async_sessionmaker", fake_sessionmaker)
    monkeypatch.setattr(module, "PostgresRuntimePolicyRepository", FakeRepository)

    monkeypatch.delenv("DATABASE_AUTHORIZATION_SIGNING_SECRET", raising=False)
    settings = DeploymentSettings(env="test", session_secret="seed-signing-secret")

    assert await module.seed_runtime_policy_heads(settings) is True
    assert captured["session_options"] == {
        "expire_on_commit": False,
        "info": {"database_authorization_signing_secret": "seed-signing-secret"},
    }
    assert captured["disposed"] is True
    knowledge = captured["initial_policy"].knowledge_base
    assert knowledge.vector_enabled is False
    assert knowledge.rerank.enabled is False
    assert knowledge.graphrag.enabled is False
    assert knowledge.ocr.mode == "off"


@pytest.fixture
def empty_runtime_policy_database(postgres_integration) -> None:
    del postgres_integration
    settings = load_deployment_settings()
    engine = create_engine(sqlalchemy_sync_migration_database_uri(settings))
    try:
        with engine.begin() as connection:
            configure_sync_system_authorization(
                connection,
                actor="runtime-policy-seed-test-reset",
                signing_secret=settings.database_authorization_signing_secret,
            )
            connection.execute(
                text(
                    "TRUNCATE runtime_policy_heads, execution_policy_revisions, "
                    "operations_policy_revisions RESTART IDENTITY CASCADE"
                )
            )
    finally:
        engine.dispose()


def _policy_counts() -> tuple[int, int, int]:
    settings = load_deployment_settings()
    engine = create_engine(sqlalchemy_sync_migration_database_uri(settings))
    try:
        with engine.begin() as connection:
            configure_sync_system_authorization(
                connection,
                actor="runtime-policy-seed-test-count",
                signing_secret=settings.database_authorization_signing_secret,
            )
            return tuple(
                connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in (
                    "runtime_policy_heads",
                    "execution_policy_revisions",
                    "operations_policy_revisions",
                )
            )
    finally:
        engine.dispose()


def _seeded_policy_state() -> dict:
    settings = load_deployment_settings()
    engine = create_engine(sqlalchemy_sync_migration_database_uri(settings))
    try:
        with engine.begin() as connection:
            configure_sync_system_authorization(
                connection,
                actor="runtime-policy-seed-test-read",
                signing_secret=settings.database_authorization_signing_secret,
            )
            row = (
                connection.execute(
                    text(
                        "SELECT head.*, execution.payload AS execution_payload, "
                        "execution.digest AS execution_digest, "
                        "execution.created_at AS execution_created_at, "
                        "operations.payload AS operations_payload, "
                        "operations.digest AS operations_digest, "
                        "operations.created_at AS operations_created_at "
                        "FROM runtime_policy_heads AS head "
                        "JOIN execution_policy_revisions AS execution "
                        "ON execution.id = head.execution_revision_id "
                        "JOIN operations_policy_revisions AS operations "
                        "ON operations.id = head.operations_revision_id"
                    )
                )
                .mappings()
                .one()
            )
            state = dict(row)
            execution = initial_execution_policy()
            operations = OperationsPolicy()
            assert state["version"] == 1
            assert state["execution_payload"] == execution.model_dump(mode="json")
            assert state["execution_digest"] == policy_digest(1, execution)
            assert state["operations_payload"] == operations.model_dump(mode="json")
            assert state["operations_digest"] == policy_digest(1, operations)
            return state
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_seed_is_atomic_and_idempotent(empty_runtime_policy_database) -> None:
    del empty_runtime_policy_database
    seed = import_module("app.migrate_runtime_policy_seed").seed_runtime_policy_heads
    settings = load_deployment_settings()

    assert await seed(settings) is True
    original = _seeded_policy_state()
    assert await seed(settings) is False
    assert _policy_counts() == (1, 1, 1)
    assert _seeded_policy_state() == original


@pytest.mark.asyncio
async def test_concurrent_seed_has_one_creator(empty_runtime_policy_database) -> None:
    del empty_runtime_policy_database
    seed = import_module("app.migrate_runtime_policy_seed").seed_runtime_policy_heads
    settings = load_deployment_settings()

    results = await asyncio.gather(seed(settings), seed(settings))

    assert sorted(results) == [False, True]
    assert _policy_counts() == (1, 1, 1)
    _seeded_policy_state()
