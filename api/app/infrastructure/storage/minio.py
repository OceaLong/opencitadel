#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import io
import logging
from datetime import timedelta
from typing import Optional
from urllib.parse import urlparse

from minio import Minio as MinioClient
from minio.error import S3Error
from starlette.concurrency import run_in_threadpool

from core.config import Settings, get_settings

logger = logging.getLogger(__name__)

_BUCKET_EXISTS_CODES = frozenset({
    "BucketAlreadyOwnedByYou",
    "BucketAlreadyExists",
})

_INIT_MAX_ATTEMPTS = 15
_INIT_RETRY_DELAY_SECONDS = 2.0
_AUTH_ERROR_CODES = frozenset({
    "AccessDenied",
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
})


class Minio:
    """MinIO 对象存储客户端。"""

    def __init__(self) -> None:
        self._settings: Settings = get_settings()
        self._client: Optional[MinioClient] = None
        self._presign_client: Optional[MinioClient] = None

    async def init(self) -> None:
        if self._client is not None:
            logger.warning("MinIO 对象存储已初始化，无需重复操作")
            return

        if self._settings.env == "test":
            logger.info("测试环境跳过 MinIO 客户端初始化")
            self._client = object()  # type: ignore[assignment]
            return

        last_exc: Exception | None = None
        for attempt in range(1, _INIT_MAX_ATTEMPTS + 1):
            try:
                client = MinioClient(
                    self._settings.minio_endpoint,
                    access_key=self._settings.minio_access_key,
                    secret_key=self._settings.minio_secret_key,
                    secure=self._settings.minio_secure,
                )
                bucket = self.bucket
                exists = await run_in_threadpool(client.bucket_exists, bucket)
                if not exists:
                    try:
                        await run_in_threadpool(client.make_bucket, bucket)
                    except S3Error as exc:
                        if exc.code not in _BUCKET_EXISTS_CODES:
                            raise
                self._client = client
                self._presign_client = self._build_presign_client()
                logger.info("MinIO 对象存储初始化成功")
                return
            except S3Error as exc:
                if exc.code in _AUTH_ERROR_CODES:
                    logger.error("MinIO 对象存储认证失败: %s", exc)
                    raise
                last_exc = exc
            except Exception as exc:
                last_exc = exc

            if attempt < _INIT_MAX_ATTEMPTS:
                logger.warning(
                    "MinIO 对象存储初始化失败 (attempt %s/%s): %s",
                    attempt,
                    _INIT_MAX_ATTEMPTS,
                    last_exc,
                )
                await asyncio.sleep(_INIT_RETRY_DELAY_SECONDS)

        logger.error("MinIO 对象存储初始化失败: %s", last_exc)
        raise last_exc  # type: ignore[misc]

    def _build_presign_client(self) -> Optional[MinioClient]:
        public_endpoint = (self._settings.minio_public_endpoint or "").strip()
        if not public_endpoint:
            return None
        if "://" not in public_endpoint:
            scheme = "https" if self._settings.minio_secure else "http"
            public_endpoint = f"{scheme}://{public_endpoint}"
        parsed = urlparse(public_endpoint)
        if not parsed.netloc:
            return None
        return MinioClient(
            parsed.netloc,
            access_key=self._settings.minio_access_key,
            secret_key=self._settings.minio_secret_key,
            secure=parsed.scheme == "https",
        )

    async def shutdown(self) -> None:
        if self._client is not None:
            self._client = None
            self._presign_client = None
            logger.info("关闭 MinIO 对象存储成功")

    @property
    def client(self) -> MinioClient:
        if self._client is None:
            raise RuntimeError("MinIO 对象存储未初始化，请调用 init() 完成初始化")
        return self._client

    @property
    def bucket(self) -> str:
        return self._settings.minio_bucket

    async def put_bytes(self, key: str, data: bytes) -> None:
        await run_in_threadpool(
            self.client.put_object,
            self.bucket,
            key,
            io.BytesIO(data),
            length=len(data),
        )

    async def get_bytes(self, key: str) -> bytes:
        response = await run_in_threadpool(
            self.client.get_object,
            self.bucket,
            key,
        )
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    async def presigned_get_url(self, key: str, expires_seconds: int = 604800) -> Optional[str]:
        if self._settings.env == "test":
            return f"https://example.com/{key}"
        if self._presign_client is None:
            return None
        return await run_in_threadpool(
            self._presign_client.presigned_get_object,
            self.bucket,
            key,
            expires=timedelta(seconds=expires_seconds),
        )
