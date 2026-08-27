from app.domain.models.inference import InferenceCapabilities
from app.domain.models.message import MediaAttachment
from app.domain.services import vision_service


class _FakeLLM:
    def __init__(self, capabilities: InferenceCapabilities):
        self._capabilities = capabilities

    @property
    def capabilities(self) -> InferenceCapabilities:
        return self._capabilities

    @property
    def supports_multimodal(self) -> bool:
        return self._capabilities.vision


def test_build_user_message_uses_image_ref_when_url_encoding():
    llm = _FakeLLM(InferenceCapabilities(vision=True, image_encoding="url"))
    message = vision_service.build_user_message(
        "describe",
        [MediaAttachment(mime_type="image/png", ref_url="https://example.com/a.png")],
        llm=llm,
    )
    assert message["content"][1]["type"] == "image_ref"


def test_inflate_messages_for_llm_converts_image_ref():
    llm = _FakeLLM(InferenceCapabilities(vision=True, image_encoding="url"))
    inflated = vision_service.inflate_messages_for_llm(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {
                        "type": "image_ref",
                        "ref": "https://example.com/a.png",
                        "mime_type": "image/png",
                    },
                ],
            },
        ],
        llm,
    )
    assert inflated[0]["content"][1]["type"] == "image_url"


def test_build_user_message_without_vision_returns_text_only():
    llm = _FakeLLM(InferenceCapabilities(vision=False))
    message = vision_service.build_user_message(
        "hello",
        [MediaAttachment(mime_type="image/png", data_base64="aW1n")],
        llm=llm,
    )
    assert message["content"] == "hello"
