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
            key="screenshots/test.png",
        ))
        monkeypatch.setattr(
            vision_service,
            "build_file_public_url",
            lambda f: "https://example.com/screenshots/test.png",
        )
        summary, extras = await vision_service.build_screenshot_messages(
            "browser_screenshot",
            {"screenshot_base64": base64.b64encode(b"pngdata").decode("ascii")},
            llm,
            file_storage=storage,
        )
        assert extras
        assert extras[0]["content"][1]["type"] == "image_ref"

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
