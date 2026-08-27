import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.domain.models.inference import InferenceCapabilities
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


def test_build_screenshot_messages_uses_ref_when_storage_provided():
    async def _run():
        llm = _FakeLLM(InferenceCapabilities(vision=True))
        storage = MagicMock()
        storage.upload_file = AsyncMock(
            return_value=SimpleNamespace(
                id="file-1",
                key="screenshots/test.png",
            )
        )
        storage.presigned_get_url = AsyncMock(
            return_value="https://example.com/screenshots/test.png"
        )
        _summary, extras = await vision_service.build_screenshot_messages(
            "browser_screenshot",
            {"screenshot_base64": base64.b64encode(b"pngdata").decode("ascii")},
            llm,
            file_storage=storage,
        )
        assert extras
        assert extras[0]["content"][1]["type"] == "image_ref"
        assert extras[0]["content"][1]["ref"] == "https://example.com/screenshots/test.png"

    asyncio.run(_run())


def test_build_screenshot_messages_falls_back_to_base64_without_presigned_url():
    async def _run():
        llm = _FakeLLM(InferenceCapabilities(vision=True))
        storage = MagicMock()
        storage.upload_file = AsyncMock(
            return_value=SimpleNamespace(
                id="file-1",
                key="screenshots/test.png",
            )
        )
        storage.presigned_get_url = AsyncMock(return_value="")
        _summary, extras = await vision_service.build_screenshot_messages(
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


def test_build_file_public_url_uses_injected_storage():
    async def _run():
        storage = MagicMock()
        storage.presigned_get_url = AsyncMock(return_value="https://example.com/images/test.png")
        file = SimpleNamespace(id="img-0", key="images/test.png")

        url = await vision_service.build_file_public_url(file, storage)

        assert url == "https://example.com/images/test.png"
        storage.presigned_get_url.assert_awaited_once_with(
            "images/test.png",
            expires_seconds=vision_service.PRESIGNED_URL_DEFAULT_EXPIRES_SECONDS,
        )

    asyncio.run(_run())


def test_upload_image_bytes_fallback_to_proxy():
    async def _run():
        storage = MagicMock()
        storage.upload_file = AsyncMock(
            return_value=SimpleNamespace(
                id="img-1",
                key="images/test.png",
            )
        )
        storage.presigned_get_url = AsyncMock(return_value="")
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
        storage.upload_file = AsyncMock(
            return_value=SimpleNamespace(
                id="img-2",
                key="images/owned.png",
            )
        )
        storage.presigned_get_url = AsyncMock(return_value="https://example.com/owned.png")

        await vision_service.upload_image_bytes_to_storage(
            storage,
            b"pngdata",
            owner_user_id="user-1",
            team_id="team-1",
        )

        payload = storage.upload_file.await_args.args[0]
        assert payload.owner_user_id == "user-1"
        assert payload.team_id == "team-1"

    asyncio.run(_run())


def test_memory_contains_image_refs():
    refs = ["https://example.com/a.png"]
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_ref", "ref": "https://example.com/a.png", "mime_type": "image/png"},
            ],
        }
    ]
    assert vision_service.memory_contains_image_refs(messages, refs) is True


def test_strip_images_for_tool_call():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "image_ref", "ref": "https://example.com/a.png"},
            ],
        }
    ]
    stripped = vision_service.strip_images_for_tool_call(messages)
    assert "图片已在先前轮次" in stripped[0]["content"]
