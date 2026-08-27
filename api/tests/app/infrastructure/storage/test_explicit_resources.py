from __future__ import annotations

import pytest

from app.infrastructure.storage import factory as storage_factory
from app.infrastructure.storage.cos import Cos
from app.infrastructure.storage.minio import Minio
from app.infrastructure.storage.postgres import Postgres
from app.infrastructure.storage.redis import RedisClient
from core.config import (
    DeploymentSettings,
    load_deployment_settings,
    sqlalchemy_sync_database_uri,
)


def test_settings_loader_returns_independent_validated_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV", "test")

    first = load_deployment_settings()
    second = load_deployment_settings()

    assert first == second
    assert first is not second


@pytest.mark.parametrize("resource_type", [Postgres, RedisClient, Cos, Minio])
def test_stateful_resource_constructors_require_settings(resource_type: type) -> None:
    settings = DeploymentSettings(env="test")

    resource = resource_type(settings)

    assert resource._settings is settings
    with pytest.raises(TypeError):
        resource_type()


def test_sync_uri_helper_requires_explicit_settings() -> None:
    settings = DeploymentSettings(
        postgres_user="app",
        postgres_password="secret",
        postgres_host="database",
    )

    assert sqlalchemy_sync_database_uri(settings).startswith(
        "postgresql+psycopg2://app:secret@database:5432/"
    )
    with pytest.raises(TypeError):
        sqlalchemy_sync_database_uri()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "expected_bucket"),
    [("cos", "cos-bucket"), ("minio", "minio-bucket")],
)
async def test_storage_factory_passes_one_settings_object_to_client(
    provider: str,
    expected_bucket: str,
) -> None:
    settings = DeploymentSettings(
        env="test",
        storage_provider=provider,
        cos_bucket="cos-bucket",
        minio_bucket="minio-bucket",
    )

    client = await storage_factory.create_storage_client(settings)
    try:
        assert client._settings is settings
        assert client.bucket == expected_bucket
    finally:
        await client.shutdown()


def test_storage_factory_has_no_active_client_registry() -> None:
    assert not hasattr(storage_factory, "set_active_storage_client")
    assert not hasattr(storage_factory, "get_active_storage_client")
