#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.external.object_storage import ObjectStoragePort
from app.infrastructure.storage.cos import Cos
from app.infrastructure.storage.minio import Minio


class CosObjectStorageAdapter(ObjectStoragePort):
    def __init__(self, cos: Cos) -> None:
        self._cos = cos

    async def put_bytes(self, key: str, data: bytes) -> None:
        await self._cos.put_bytes(key, data)

    async def get_bytes(self, key: str) -> bytes:
        return await self._cos.get_bytes(key)


class MinioObjectStorageAdapter(ObjectStoragePort):
    def __init__(self, minio: Minio) -> None:
        self._minio = minio

    async def put_bytes(self, key: str, data: bytes) -> None:
        await self._minio.put_bytes(key, data)

    async def get_bytes(self, key: str) -> bytes:
        return await self._minio.get_bytes(key)
