#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
from functools import lru_cache
from typing import Any, Optional

from qcloud_cos import CosS3Client, CosConfig
from starlette.concurrency import run_in_threadpool

from core.config import Settings, get_settings

logger = logging.getLogger(__name__)

_READ_CHUNK_SIZE = 65536
_GET_BYTES_MAX_ATTEMPTS = 3
_GET_BYTES_RETRY_DELAYS_SECONDS = (0.2, 0.5, 1.0)


def _read_body_fully(body: Any) -> bytes:
    if not hasattr(body, "read"):
        return bytes(body)
    chunks: list[bytes] = []
    while True:
        chunk = body.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_content_length(response: dict) -> Optional[int]:
    expected_length = response.get("Content-Length")
    if expected_length is None:
        return None
    try:
        return int(expected_length)
    except (TypeError, ValueError):
        return None


class Cos:
    """腾讯云Cos对象存储"""

    def __init__(self):
        """构造函数，完成配置获取+Cos客户端初始化赋值"""
        self._settings: Settings = get_settings()
        self._client: Optional[CosS3Client] = None

    async def init(self) -> None:
        """完成cos腾讯云对象存储客户端的创建"""
        if self._client is not None:
            logger.warning("Cos腾讯云对象存储已初始化，无需重复操作")
            return

        if self._settings.env == "test":
            logger.info("测试环境跳过 Cos 客户端初始化")
            self._client = object()  # type: ignore[assignment]
            return

        try:
            # 2.创建cos配置
            config = CosConfig(
                Region=self._settings.cos_region,
                SecretId=self._settings.cos_secret_id,
                SecretKey=self._settings.cos_secret_key,
                Token=None,
                Scheme=self._settings.cos_scheme,
            )
            self._client = CosS3Client(config)
            logger.info("Cos腾讯云对象存储初始化成功")
        except Exception as e:
            logger.error(f"Cos腾讯云对象存储初始化失败: {str(e)}")
            raise

    async def shutdown(self) -> None:
        """关闭cos腾讯云对象存储"""
        if self._client is not None:
            self._client = None
            logger.info("关闭腾讯云Cos对象存储成功")

        get_cos.cache_clear()

    @property
    def client(self) -> CosS3Client:
        """只读属性，返回腾讯云Cos对象存储客户端"""
        if self._client is None:
            raise RuntimeError("腾讯云Cos对象存储未初始化，请调用init()完成初始化")
        return self._client

    @property
    def bucket(self) -> str:
        return self._settings.cos_bucket

    async def put_bytes(self, key: str, data: bytes) -> None:
        """Upload raw bytes to COS without creating a file record."""
        logger.debug("COS put_bytes key=%s byte_size=%d", key, len(data))
        await run_in_threadpool(
            self.client.put_object,
            Bucket=self.bucket,
            Body=data,
            Key=key,
        )

    async def _fetch_object_bytes(self, key: str) -> tuple[bytes, Optional[int]]:
        response = await run_in_threadpool(
            self.client.get_object,
            Bucket=self.bucket,
            Key=key,
        )
        body = response["Body"]
        expected_length = _parse_content_length(response)
        data = _read_body_fully(body)
        return data, expected_length

    async def get_bytes(self, key: str) -> bytes:
        """Download raw bytes from COS."""
        last_data = b""
        last_expected_length: Optional[int] = None

        for attempt in range(_GET_BYTES_MAX_ATTEMPTS):
            if attempt > 0:
                delay = _GET_BYTES_RETRY_DELAYS_SECONDS[attempt - 1]
                logger.warning(
                    "COS get_bytes 重试 key=%s attempt=%s/%s delay=%.1fs",
                    key,
                    attempt + 1,
                    _GET_BYTES_MAX_ATTEMPTS,
                    delay,
                )
                await asyncio.sleep(delay)

            data, expected_length = await self._fetch_object_bytes(key)
            last_data = data
            last_expected_length = expected_length

            if expected_length is not None and len(data) != expected_length:
                logger.warning(
                    "COS get_bytes 长度不一致 key=%s expected=%s got=%d",
                    key,
                    expected_length,
                    len(data),
                )
                continue

            return data

        raise RuntimeError(
            f"COS get_bytes 读取失败 key={key} "
            f"expected={last_expected_length} got={len(last_data)}"
        )

    async def presigned_get_url(self, key: str, expires_seconds: int = 604800) -> Optional[str]:
        """Generate a presigned download URL for LLM-accessible image references."""
        if self._settings.env == "test":
            return f"https://example.com/{key}"
        return await run_in_threadpool(
            self.client.get_presigned_download_url,
            Bucket=self.bucket,
            Key=key,
            Expired=expires_seconds,
        )


@lru_cache()
def get_cos() -> Cos:
    """获取腾讯云cos对象存储"""
    return Cos()
