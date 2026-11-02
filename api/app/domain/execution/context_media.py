"""Bounded inline image protocol; no remote URL fetch or implicit media conversion."""

import base64
from io import BytesIO

from PIL import Image

MAX_CONTEXT_IMAGE_BYTES = 262144
# GPT-4.1 high detail fits within 2048² before 512px tiling: <=16*170+85.
# Reserve 4096 per image in addition to all serialized bytes and framing.
# Reviewed 2026-10-16: https://developers.openai.com/api/docs/guides/images-vision
GPT41_IMAGE_TOKEN_BOUND = 4096


def inline_image_part(data, mime_type):
    if not isinstance(data, bytes) or len(data) > MAX_CONTEXT_IMAGE_BYTES:
        raise ValueError("CONTEXT_IMAGE_UNSUPPORTED")
    part = {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime_type};base64," + base64.b64encode(data).decode("ascii"),
            "detail": "high",
        },
    }
    validate_inline_image(part)
    return part


def validate_inline_image(part):
    try:
        if (
            not isinstance(part, dict)
            or set(part) != {"type", "image_url"}
            or part["type"] != "image_url"
        ):
            raise ValueError
        image = part["image_url"]
        if (
            not isinstance(image, dict)
            or set(image) != {"url", "detail"}
            or image["detail"] != "high"
        ):
            raise ValueError
        url = image["url"]
        if not isinstance(url, str) or len(url) > MAX_CONTEXT_IMAGE_BYTES * 4 // 3 + 64:
            raise ValueError
        header, encoded = url.split(",", 1)
        if header not in {"data:image/png;base64", "data:image/jpeg;base64"}:
            raise ValueError
        data = base64.b64decode(encoded, validate=True)
        if not data or len(data) > MAX_CONTEXT_IMAGE_BYTES:
            raise ValueError
        with Image.open(BytesIO(data)) as decoded:
            if (
                decoded.format != ("PNG" if "image/png" in header else "JPEG")
                or getattr(decoded, "n_frames", 1) != 1
                or not all(0 < dimension <= 4096 for dimension in decoded.size)
            ):
                raise ValueError
            decoded.verify()
        return data
    except (KeyError, TypeError, ValueError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("CONTEXT_IMAGE_UNSUPPORTED") from error
