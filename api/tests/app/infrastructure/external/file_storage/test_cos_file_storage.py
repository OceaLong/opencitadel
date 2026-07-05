#!/usr/bin/env python
# -*- coding: utf-8 -*-
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.file import File
from app.infrastructure.external.file_storage.cos_file_storage import CosFileStorage


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_download_file_reads_full_object_via_get_bytes():
    full_data = b"%PDF-1.4" + b"x" * 2048
    stored = File(
        id="file-1",
        filename="doc.pdf",
        key="2026/07/05/file-1.pdf",
        mime_type="application/pdf",
        size=len(full_data),
    )
    uow = MagicMock()
    uow.file.get_by_id = AsyncMock(return_value=stored)
    uow_context = MagicMock()
    uow_context.__aenter__ = AsyncMock(return_value=uow)
    uow_context.__aexit__ = AsyncMock(return_value=None)

    cos = MagicMock()
    cos.get_bytes = AsyncMock(return_value=full_data)

    storage = CosFileStorage(bucket="test-bucket", cos=cos, uow_factory=lambda: uow_context)
    stream, file_info = await storage.download_file("file-1")

    assert file_info.id == "file-1"
    assert isinstance(stream, BytesIO)
    assert stream.read() == full_data
    cos.get_bytes.assert_awaited_once_with(stored.key)


@pytest.mark.anyio
async def test_download_file_propagates_get_bytes_truncation_error():
    stored = File(
        id="file-2",
        filename="doc.pdf",
        key="2026/07/05/file-2.pdf",
        mime_type="application/pdf",
        size=4096,
    )
    uow = MagicMock()
    uow.file.get_by_id = AsyncMock(return_value=stored)
    uow_context = MagicMock()
    uow_context.__aenter__ = AsyncMock(return_value=uow)
    uow_context.__aexit__ = AsyncMock(return_value=None)

    cos = MagicMock()
    cos.get_bytes = AsyncMock(
        side_effect=RuntimeError("COS get_bytes 读取失败 key=2026/07/05/file-2.pdf expected=4096 got=1024")
    )

    storage = CosFileStorage(bucket="test-bucket", cos=cos, uow_factory=lambda: uow_context)

    with pytest.raises(RuntimeError, match="COS get_bytes 读取失败"):
        await storage.download_file("file-2")
