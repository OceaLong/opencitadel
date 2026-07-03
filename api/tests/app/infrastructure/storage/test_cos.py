#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

import pytest

from app.infrastructure.storage.cos import Cos, _read_body_fully


class _ChunkedBody:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    def read(self, size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        if size <= 0:
            return b"".join(self._chunks.pop(0) for _ in range(len(self._chunks)))
        return self._chunks.pop(0)


def test_read_body_fully_reads_all_chunks():
    body = _ChunkedBody([b"a" * 1024, b"b" * 1024, b"c"])
    assert len(_read_body_fully(body)) == 2049


async def _immediate_sleep(_seconds: float) -> None:
    return None


def _make_cos_with_client() -> Cos:
    cos = Cos()
    cos._client = MagicMock()
    cos._settings = MagicMock()
    cos._settings.cos_bucket = "test-bucket"
    return cos


@pytest.mark.asyncio
async def test_get_bytes_recovers_after_truncated_reads():
    full_data = b"x" * 2854
    truncated = full_data[:1024]
    call_count = {"n": 0}

    def get_object(**_kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            body = _ChunkedBody([truncated])
        else:
            body = _ChunkedBody([full_data])
        return {"Body": body, "Content-Length": str(len(full_data))}

    cos = _make_cos_with_client()
    cos._client.get_object = get_object

    async def fake_run_in_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    with patch("app.infrastructure.storage.cos.asyncio.sleep", new=_immediate_sleep):
        with patch(
            "app.infrastructure.storage.cos.run_in_threadpool",
            side_effect=fake_run_in_threadpool,
        ):
            result = await cos.get_bytes("artifacts/s1/a1/v1.md")

    assert result == full_data
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_get_bytes_raises_when_truncation_persists():
    full_data = b"y" * 2854
    truncated = full_data[:1024]

    def get_object(**_kwargs):
        return {"Body": _ChunkedBody([truncated]), "Content-Length": str(len(full_data))}

    cos = _make_cos_with_client()
    cos._client.get_object = get_object

    async def fake_run_in_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    with patch("app.infrastructure.storage.cos.asyncio.sleep", new=_immediate_sleep):
        with patch(
            "app.infrastructure.storage.cos.run_in_threadpool",
            side_effect=fake_run_in_threadpool,
        ):
            with pytest.raises(RuntimeError, match="COS get_bytes 读取失败"):
                await cos.get_bytes("artifacts/s1/a1/v1.md")


@pytest.mark.asyncio
async def test_put_bytes_passes_content_length():
    cos = _make_cos_with_client()
    captured: dict = {}

    def put_object(**kwargs):
        captured.update(kwargs)

    cos._client.put_object = put_object

    async def fake_run_in_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    data = b"artifact payload"

    with patch(
        "app.infrastructure.storage.cos.run_in_threadpool",
        side_effect=fake_run_in_threadpool,
    ):
        await cos.put_bytes("artifacts/s1/a1/v1.md", data)

    assert captured["ContentLength"] == len(data)
    assert captured["Body"] == data
