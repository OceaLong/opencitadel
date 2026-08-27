import asyncio
from importlib import import_module

import pytest
from sqlalchemy import create_engine, text

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
            return True

    def fake_sessionmaker(_engine, **kwargs):
        captured["session_options"] = kwargs
        return object()

    monkeypatch.setattr(module, "create_async_engine", lambda *_args, **_kwargs: FakeEngine())
    monkeypatch.setattr(module, "async_sessionmaker", fake_sessionmaker)
    monkeypatch.setattr(module, "PostgresRuntimePolicyRepository", FakeRepository)

    settings = DeploymentSettings(env="test", session_secret="seed-signing-secret")

    assert await module.seed_runtime_policy_heads(settings) is True
    assert captured["session_options"] == {
        "expire_on_commit": False,
        "info": {"database_authorization_signing_secret": "seed-signing-secret"},
    }
    assert captured["disposed"] is True


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
                signing_secret=settings.session_secret,
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
                signing_secret=settings.session_secret,
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


@pytest.mark.asyncio
async def test_seed_is_atomic_and_idempotent(empty_runtime_policy_database) -> None:
    del empty_runtime_policy_database
    seed = import_module("app.migrate_runtime_policy_seed").seed_runtime_policy_heads
    settings = load_deployment_settings()

    assert await seed(settings) is True
    assert await seed(settings) is False
    assert _policy_counts() == (1, 1, 1)


@pytest.mark.asyncio
async def test_concurrent_seed_has_one_creator(empty_runtime_policy_database) -> None:
    del empty_runtime_policy_database
    seed = import_module("app.migrate_runtime_policy_seed").seed_runtime_policy_heads
    settings = load_deployment_settings()

    results = await asyncio.gather(seed(settings), seed(settings))

    assert sorted(results) == [False, True]
    assert _policy_counts() == (1, 1, 1)
