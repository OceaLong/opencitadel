#!/usr/bin/env python
# -*- coding: utf-8 -*-
import base64

from app.domain.models.message import VisionAttachment
from app.domain.utils.vision import (
    build_image_content_part,
    build_user_message,
    is_image_mime,
)


def test_is_image_mime():
    assert is_image_mime("image/png") is True
    assert is_image_mime("application/pdf") is False


def test_build_image_content_part():
    part = build_image_content_part(b"abc", "image/png")
    assert part["type"] == "image_url"
    assert "data:image/png;base64," in part["image_url"]["url"]


def test_build_user_message_with_vision_attachments():
    attachment = VisionAttachment(
        mime_type="image/png",
        data_base64=base64.b64encode(b"png").decode("ascii"),
    )
    message = build_user_message(
        "describe this",
        [attachment],
        supports_multimodal=True,
    )
    assert message["role"] == "user"
    assert isinstance(message["content"], list)
    assert len(message["content"]) == 2


def test_build_user_message_without_multimodal_flag():
    attachment = VisionAttachment(
        mime_type="image/png",
        data_base64=base64.b64encode(b"png").decode("ascii"),
    )
    message = build_user_message(
        "describe this",
        [attachment],
        supports_multimodal=False,
    )
    assert message["content"] == "describe this"
