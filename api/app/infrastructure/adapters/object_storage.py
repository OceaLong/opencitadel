#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.external.object_storage import ObjectStoragePort
from app.infrastructure.storage.cos import Cos


class CosObjectStorageAdapter(ObjectStoragePort):
    def __init__(self, cos: Cos) -> None:
        self._cos = cos

    async def put_bytes(self, key: str, data: bytes) -> None:
        await self._cos.put_bytes(key, data)

    async def get_bytes(self, key: str) -> bytes:
        return await self._cos.get_bytes(key)
