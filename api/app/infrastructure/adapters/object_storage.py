from collections.abc import Callable

from minio.error import S3Error
from qcloud_cos.cos_exception import CosServiceError
from starlette.concurrency import run_in_threadpool

from app.domain.external.object_storage import (
    BoundedObjectBytes,
    ObjectNotFoundError,
    ObjectStoragePort,
)
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
        try:
            return await self._cos.get_bytes(key)
        except CosServiceError as error:
            details = error.get_digest_msg()
            if isinstance(details, dict) and details.get("code") == "NoSuchKey":
                raise ObjectNotFoundError("object_not_found") from error
            raise

    async def get_bounded_bytes(
        self, key: str, limit: int, *, observed: Callable[[int, bytes], None] | None = None
    ) -> BoundedObjectBytes:
        _bounded_limit(limit)

        def read():
            response = self._cos.client.get_object(Bucket=self._cos.bucket, Key=key)
            return _bounded_response(response["Body"], limit, observed=observed)

        try:
            return await run_in_threadpool(read)
        except CosServiceError as error:
            details = error.get_digest_msg()
            if isinstance(details, dict) and details.get("code") == "NoSuchKey":
                raise ObjectNotFoundError("object_not_found") from error
            raise

    async def delete_bytes(self, key: str) -> None:
        await self._cos.delete_bytes(key)


class MinioObjectStorageAdapter(ObjectStoragePort):
    def __init__(self, minio: Minio) -> None:
        self._minio = minio

    async def put_bytes(self, key: str, data: bytes) -> None:
        await self._minio.put_bytes(key, data)

    async def get_bytes(self, key: str) -> bytes:
        try:
            return await self._minio.get_bytes(key)
        except S3Error as error:
            if error.code == "NoSuchKey":
                raise ObjectNotFoundError("object_not_found") from error
            raise

    async def get_bounded_bytes(
        self, key: str, limit: int, *, observed: Callable[[int, bytes], None] | None = None
    ) -> BoundedObjectBytes:
        _bounded_limit(limit)

        def read():
            response = self._minio.client.get_object(self._minio.bucket, key)
            return _bounded_response(response, limit, observed=observed)

        try:
            return await run_in_threadpool(read)
        except S3Error as error:
            if error.code == "NoSuchKey":
                raise ObjectNotFoundError("object_not_found") from error
            raise

    async def delete_bytes(self, key: str) -> None:
        await self._minio.delete_bytes(key)


def _bounded_response(response, limit, *, observed=None):
    stream = response.get_raw_stream() if hasattr(response, "get_raw_stream") else response
    parts, length = [], 0
    try:
        while length <= limit:
            chunk = stream.read(min(65536, limit + 1 - length))
            if not chunk:
                break
            if len(chunk) > min(65536, limit + 1 - length):
                raise OSError("object_reader_budget_violation")
            if observed is not None:
                observed(length, chunk)
            parts.append(chunk)
            length += len(chunk)
        data = b"".join(parts)
        return BoundedObjectBytes(data[:limit], length > limit)
    finally:
        try:
            stream.close()
        finally:
            if hasattr(stream, "release_conn"):
                stream.release_conn()


def _bounded_limit(limit):
    if type(limit) is not int or not 1 <= limit <= 2097152:
        raise ValueError("invalid_object_read_limit")
