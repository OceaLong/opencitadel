#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.llm_model import ModelCapabilities
from app.domain.models.message import MediaAttachment
from app.domain.services import vision_service


class _FakeLLM:
    def __init__(self, capabilities: ModelCapabilities):
        self._capabilities = capabilities

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._capabilities

    @property
    def supports_multimodal(self) -> bool:
        return self._capabilities.vision


def test_build_screenshot_messages_uses_ref_when_storage_provided(monkeypatch):
    async def _run():
        llm = _FakeLLM(ModelCapabilities(vision=True))
        storage = MagicMock()
        storage.upload_file = AsyncMock(return_value=SimpleNamespace(
            id="file-1",
            key="screenshots/test.png",
        ))

        async def _fake_public_url(_file, *, expires_seconds=3600):
            return "https://example.com/screenshots/test.png"

        monkeypatch.setattr(vision_service, "build_file_public_url", _fake_public_url)
        summary, extras = await vision_service.build_screenshot_messages(
            "browser_screenshot",
            {"screenshot_base64": base64.b64encode(b"pngdata").decode("ascii")},
            llm,
            file_storage=storage,
        )
        assert extras
        assert extras[0]["content"][1]["type"] == "image_ref"
        assert extras[0]["content"][1]["ref"] == "https://example.com/screenshots/test.png"

    asyncio.run(_run())


def test_build_screenshot_messages_falls_back_to_base64_without_presigned_url(monkeypatch):
    async def _run():
        llm = _FakeLLM(ModelCapabilities(vision=True))
        storage = MagicMock()
        storage.upload_file = AsyncMock(return_value=SimpleNamespace(
            id="file-1",
            key="screenshots/test.png",
        ))

        async def _fake_public_url(_file, *, expires_seconds=3600):
            return ""

        monkeypatch.setattr(vision_service, "build_file_public_url", _fake_public_url)
        summary, extras = await vision_service.build_screenshot_messages(
            "browser_screenshot",
            {"screenshot_base64": base64.b64encode(b"pngdata").decode("ascii")},
            llm,
            file_storage=storage,
        )
        assert extras
        assert extras[0]["content"][1]["type"] != "image_ref"

    asyncio.run(_run())


def test_build_file_proxy_url():
    file = SimpleNamespace(id="abc-123")
    assert vision_service.build_file_proxy_url(file) == "/api/files/abc-123/download"


def test_upload_image_bytes_fallback_to_proxy(monkeypatch):
    async def _run():
        storage = MagicMock()
        storage.upload_file = AsyncMock(return_value=SimpleNamespace(
            id="img-1",
            key="images/test.png",
        ))

        async def _fake_public_url(_file, *, expires_seconds=604800):
            return ""

        monkeypatch.setattr(vision_service, "build_file_public_url", _fake_public_url)
        url = await vision_service.upload_image_bytes_to_storage(
            storage,
            b"pngdata",
            fallback_to_proxy=True,
        )
        assert url == "/api/files/img-1/download"

    asyncio.run(_run())


def test_upload_image_bytes_passes_owner():
    async def _run():
        storage = MagicMock()
        storage.upload_file = AsyncMock(return_value=SimpleNamespace(
            id="img-2",
            key="images/owned.png",
        ))

        async def _fake_public_url(_file, *, expires_seconds=604800):
            return "https://example.com/owned.png"

        import app.domain.services.vision_service as vs
        original = vs.build_file_public_url
        vs.build_file_public_url = _fake_public_url
        try:
            await vs.upload_image_bytes_to_storage(
                storage,
                b"pngdata",
                owner_user_id="user-1",
                team_id="team-1",
            )
        finally:
            vs.build_file_public_url = original

        payload = storage.upload_file.await_args.args[0]
        assert payload.owner_user_id == "user-1"
        assert payload.team_id == "team-1"

    asyncio.run(_run())


def test_memory_contains_image_refs():
    refs = ["https://example.com/a.png"]
    messages = [{
        "role": "user",
        "content": [
            {"type": "image_ref", "ref": "https://example.com/a.png", "mime_type": "image/png"},
        ],
    }]
    assert vision_service.memory_contains_image_refs(messages, refs) is True


def test_strip_images_for_tool_call():
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "image_ref", "ref": "https://example.com/a.png"},
        ],
    }]
    stripped = vision_service.strip_images_for_tool_call(messages)
    assert "图片已在先前轮次" in stripped[0]["content"]
