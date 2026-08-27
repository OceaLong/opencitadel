from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.storage.cos import Cos
    from app.infrastructure.storage.minio import Minio

    StorageClient = Cos | Minio
else:
    StorageClient = object


async def create_storage_client(settings) -> StorageClient:
    provider = (settings.storage_provider or "cos").strip().lower()
    if provider == "minio":
        from app.infrastructure.storage.minio import Minio

        client = Minio(settings)
    else:
        from app.infrastructure.storage.cos import Cos

        client = Cos(settings)
    await client.init()
    return client
