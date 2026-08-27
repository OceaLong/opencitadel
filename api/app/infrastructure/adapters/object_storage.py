from app.domain.external.object_storage import ObjectStoragePort
from app.infrastructure.storage.cos import Cos
from app.infrastructure.storage.minio import Minio

ObjectStorageClient = Cos | Minio


def create_object_storage_adapter(
    *,
    provider: str,
    client: ObjectStorageClient,
) -> ObjectStoragePort:
    normalized = provider.strip().lower()
    if normalized == "minio":
        return MinioObjectStorageAdapter(minio=client)  # type: ignore[arg-type]
    if normalized == "cos":
        return CosObjectStorageAdapter(cos=client)  # type: ignore[arg-type]
    raise ValueError(f"unsupported storage provider: {provider}")


class CosObjectStorageAdapter(ObjectStoragePort):
    def __init__(self, cos: Cos) -> None:
        self._cos = cos

    async def put_bytes(self, key: str, data: bytes) -> None:
        await self._cos.put_bytes(key, data)

    async def get_bytes(self, key: str) -> bytes:
        return await self._cos.get_bytes(key)

    async def delete_bytes(self, key: str) -> None:
        await self._cos.delete_bytes(key)


class MinioObjectStorageAdapter(ObjectStoragePort):
    def __init__(self, minio: Minio) -> None:
        self._minio = minio

    async def put_bytes(self, key: str, data: bytes) -> None:
        await self._minio.put_bytes(key, data)

    async def get_bytes(self, key: str) -> bytes:
        return await self._minio.get_bytes(key)

    async def delete_bytes(self, key: str) -> None:
        await self._minio.delete_bytes(key)
