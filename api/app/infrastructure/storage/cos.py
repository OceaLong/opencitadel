#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from functools import lru_cache
from typing import Optional

from qcloud_cos import CosS3Client, CosConfig
from starlette.concurrency import run_in_threadpool

from core.config import Settings, get_settings

logger = logging.getLogger(__name__)


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
        await run_in_threadpool(
            self.client.put_object,
            Bucket=self.bucket,
            Body=data,
            Key=key,
        )

    async def get_bytes(self, key: str) -> bytes:
        """Download raw bytes from COS."""
        response = await run_in_threadpool(
            self.client.get_object,
            Bucket=self.bucket,
            Key=key,
        )
        body = response["Body"]
        return body.read() if hasattr(body, "read") else bytes(body)

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
