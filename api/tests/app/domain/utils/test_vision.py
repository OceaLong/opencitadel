#!/usr/bin/env python
# -*- coding: utf-8 -*-
import base64

from app.domain.models.message import VisionAttachment
from app.domain.utils.vision import (
    MAX_VISION_IMAGE_BYTES,
    build_image_content_part,
    build_user_message,
    filter_valid_vision_attachments,
    is_image_mime,
    vision_attachment_byte_size,
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


def test_filter_valid_vision_attachments_skips_oversized_image():
    oversized = VisionAttachment(
        mime_type="image/png",
        data_base64=base64.b64encode(b"x" * (MAX_VISION_IMAGE_BYTES + 1)).decode("ascii"),
    )
    valid = filter_valid_vision_attachments([oversized])
    assert valid == []


def test_build_user_message_skips_oversized_image():
    oversized = VisionAttachment(
        mime_type="image/png",
        data_base64=base64.b64encode(b"x" * (MAX_VISION_IMAGE_BYTES + 1)).decode("ascii"),
    )
    message = build_user_message(
        "describe this",
        [oversized],
        supports_multimodal=True,
    )
    assert message["content"] == "describe this"


def test_vision_attachment_byte_size():
    attachment = VisionAttachment(
        mime_type="image/png",
        data_base64=base64.b64encode(b"png").decode("ascii"),
    )
    assert vision_attachment_byte_size(attachment) == 3
