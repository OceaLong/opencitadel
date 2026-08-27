import base64

from app.domain.models.message import MediaAttachment
from app.domain.utils.vision import (
    MAX_VISION_IMAGE_BYTES,
    build_image_content_part,
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


def test_filter_valid_vision_attachments_skips_oversized_image():
    oversized = MediaAttachment(
        mime_type="image/png",
        data_base64=base64.b64encode(b"x" * (MAX_VISION_IMAGE_BYTES + 1)).decode("ascii"),
    )
    valid = filter_valid_vision_attachments([oversized])
    assert valid == []


def test_vision_attachment_byte_size():
    attachment = MediaAttachment(
        mime_type="image/png",
        data_base64=base64.b64encode(b"png").decode("ascii"),
    )
    assert vision_attachment_byte_size(attachment) == 3
