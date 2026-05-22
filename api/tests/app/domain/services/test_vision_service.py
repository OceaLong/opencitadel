#!/usr/bin/env python
# -*- coding: utf-8 -*-
from types import SimpleNamespace

from app.domain.models.llm_model import ModelCapabilities
from app.domain.models.message import VisionAttachment
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


def test_build_user_message_uses_image_ref_when_url_encoding():
    llm = _FakeLLM(ModelCapabilities(vision=True, image_encoding="url"))
    message = vision_service.build_user_message(
        "describe",
        [VisionAttachment(mime_type="image/png", ref_url="https://example.com/a.png")],
        llm=llm,
    )
    assert message["content"][1]["type"] == "image_ref"


def test_inflate_messages_for_llm_converts_image_ref():
    llm = _FakeLLM(ModelCapabilities(vision=True, image_encoding="url"))
    inflated = vision_service.inflate_messages_for_llm([
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_ref", "ref": "https://example.com/a.png", "mime_type": "image/png"},
            ],
        },
    ], llm)
    assert inflated[0]["content"][1]["type"] == "image_url"


def test_build_user_message_without_vision_returns_text_only():
    llm = _FakeLLM(ModelCapabilities(vision=False))
    message = vision_service.build_user_message(
        "hello",
        [VisionAttachment(mime_type="image/png", data_base64="aW1n")],
        llm=llm,
    )
    assert message["content"] == "hello"
