from io import BytesIO

import pytest
from PIL import Image

from app.domain.execution.context_media import inline_image_part, validate_inline_image
from app.domain.execution.context_models import ContextMessage, ContextPolicy
from tests.app.application.execution.test_context_compiler import PROFILE, compile, source


def picture():
    output = BytesIO()
    Image.new("RGB", (32, 32), color=(20, 80, 140)).save(output, format="PNG")
    return output.getvalue()


def test_image_is_protected_and_charged_against_every_frozen_candidate():
    image = inline_image_part(picture(), "image/png")
    message = ContextMessage(role="user", content="图片中的约束", media_parts=(image,))
    attachment = source(1, "", kind="attachment", protected=True, messages=[message])
    profile = PROFILE.model_copy(update={"image_profile": "gpt41-inline-high-v1"})
    result = compile([attachment], candidates=(profile,)).manifest
    assert result.estimated_tokens == result.serialized_bytes + profile.envelope_tokens + 4096
    assert result.messages[0].wire()["content"][1] == image
    assert (
        compile([attachment], candidates=(profile, PROFILE)).failure_code
        == "CONTEXT_MEDIA_PROFILE_UNSUPPORTED"
    )
    assert (
        compile(
            [attachment], candidates=(profile,), policy=ContextPolicy(max_input_tokens=4096)
        ).failure_code
        == "CONTEXT_REQUIRED_CONTENT_TOO_LARGE"
    )
    with pytest.raises(ValueError, match="protected attachment"):
        source(1, "", kind="attachment", messages=[message])


@pytest.mark.parametrize(
    "part",
    [
        {
            "type": "image_url",
            "image_url": {"url": "https://example.com/image.png", "detail": "high"},
        },
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,bm90LWFuLWltYWdl", "detail": "high"},
        },
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,!!!", "detail": "high"}},
    ],
)
def test_unverified_remote_or_invalid_image_never_enters_context(part):
    with pytest.raises(ValueError, match="CONTEXT_IMAGE_UNSUPPORTED"):
        validate_inline_image(part)
