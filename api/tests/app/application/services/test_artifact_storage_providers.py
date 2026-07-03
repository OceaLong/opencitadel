#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.services.artifact_service import ArtifactService
from app.container import _create_object_storage
from app.domain.models.artifact import Artifact
from app.infrastructure.adapters.object_storage import (
    CosObjectStorageAdapter,
    MinioObjectStorageAdapter,
)


def _artifact_uow(artifact: Artifact | None = None):
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.artifact.get_by_id = AsyncMock(return_value=artifact)
    uow.artifact.save = AsyncMock()
    uow.commit = AsyncMock()
    return uow


def _artifact_uow_with_saved_lookup():
    uow = _artifact_uow()
    saved: dict[str, Artifact] = {}

    async def save(artifact: Artifact) -> None:
        saved[artifact.id] = artifact

    async def get_by_id(artifact_id: str) -> Artifact | None:
        return saved.get(artifact_id)

    uow.artifact.save = AsyncMock(side_effect=save)
    uow.artifact.get_by_id = AsyncMock(side_effect=get_by_id)
    return uow


@pytest.mark.parametrize(
    ("provider", "adapter_cls"),
    [
        ("cos", CosObjectStorageAdapter),
        ("minio", MinioObjectStorageAdapter),
    ],
)
def test_create_object_storage_respects_provider(provider, adapter_cls):
    settings = MagicMock()
    settings.storage_provider = provider
    client = MagicMock()
    adapter = _create_object_storage(client, settings)
    assert isinstance(adapter, adapter_cls)


@pytest.mark.parametrize(
    ("adapter_cls", "client_kwarg"),
    [
        (CosObjectStorageAdapter, "cos"),
        (MinioObjectStorageAdapter, "minio"),
    ],
)
def test_artifact_write_and_read_via_storage_adapter(adapter_cls, client_kwarg):
    object_store: dict[str, bytes] = {}

    async def put_bytes(key: str, data: bytes) -> None:
        object_store[key] = data

    async def get_bytes(key: str) -> bytes:
        return object_store[key]

    client = AsyncMock()
    client.put_bytes = AsyncMock(side_effect=put_bytes)
    client.get_bytes = AsyncMock(side_effect=get_bytes)

    adapter = adapter_cls(**{client_kwarg: client})
    uow = _artifact_uow_with_saved_lookup()
    service = ArtifactService(lambda: uow, object_storage=adapter)

    async def _run():
        artifact, _ = await service.write_content(
            session_id="s1",
            artifact_id=None,
            kind="doc",
            title="Report",
            content="# Hello",
        )
        data = await service.get_content(artifact.id)
        assert data == b"# Hello"
        assert artifact.storage_ref in object_store
        client.put_bytes.assert_awaited_once()
        assert client.get_bytes.await_count >= 2

    asyncio.run(_run())
