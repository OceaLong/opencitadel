#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from app.infrastructure.storage.cos import Cos
    from app.infrastructure.storage.minio import Minio

    StorageClient = Union[Cos, Minio]
else:
    StorageClient = object

_active_storage_client: Optional[StorageClient] = None


def set_active_storage_client(client: Optional[StorageClient]) -> None:
    global _active_storage_client
    _active_storage_client = client


def get_active_storage_client() -> Optional[StorageClient]:
    return _active_storage_client


async def create_storage_client(settings) -> StorageClient:
    provider = (settings.storage_provider or "cos").strip().lower()
    if provider == "minio":
        from app.infrastructure.storage.minio import Minio

        client = Minio()
    else:
        from app.infrastructure.storage.cos import Cos

        client = Cos()
    await client.init()
    return client
