#!/usr/bin/env python
# -*- coding: utf-8 -*-
import io
from unittest.mock import AsyncMock

import pytest

from app.application.errors.exceptions import NotFoundError
from app.application.services.file_service import FileService
from app.application.services.marketplace_service import MarketplaceService
from app.domain.models.file import File
from app.domain.models.llm_model import LLMModel
from app.domain.models.scope import OwnerScope


class FailingLLMModelService:
    async def resolve_model(self, model_id=None, *, scope=None):
        raise RuntimeError("no model in unit test")


class UnusedFileService:
    pass


class FakeUow:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def fake_uow_factory():
    return FakeUow()


@pytest.fixture
def service():
    return MarketplaceService(
        llm_model_service=FailingLLMModelService(),
        file_service=UnusedFileService(),
        uow_factory=fake_uow_factory,
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_list_apps_exposes_rich_registry(service):
    apps = service.list_apps()
    assert len(apps) >= 5
    assert {"tags", "featured", "accent", "needs_vision", "examples"}.issubset(apps[0])
    assert {app["id"] for app in apps} >= {"smart-translation", "nutrition-analysis"}


@pytest.mark.anyio
async def test_route_request_falls_back_to_heuristic(service):
    route = await service.route_request(
        "帮我翻译这段英文为中文",
        scope=OwnerScope.personal("user-1"),
    )
    assert route["app_id"] == "smart-translation"
    assert route["confidence"] > 0
    assert "suggestions" in route


def test_correct_consumption_extracts_natural_language_total(service):
    result = service.correct_consumption("其实净含量是 1.2kg", serving_grams=60)
    assert result["recognized"] is True
    assert result["total_grams"] == 1200
    assert result["full_servings"] == 20


class _UnauthorizedFileRepo:
    async def get_by_id(self, file_id: str, scope=None):
        return None


class _FileUow(FakeUow):
    file = _UnauthorizedFileRepo()


class _VictimFileStorage:
    def __init__(
        self,
        *,
        filename: str = "secret.txt",
        mime_type: str = "text/plain",
    ):
        self._filename = filename
        self._mime_type = mime_type

    async def download_file(self, file_id: str):
        return (
            io.BytesIO(b"victim tenant secret"),
            File(
                id=file_id,
                filename=self._filename,
                mime_type=self._mime_type,
                owner_user_id="victim-user",
            ),
        )

    async def upload_file(self, upload):
        return File(
            id="result-file",
            filename=upload.filename,
            mime_type=upload.content_type,
            owner_user_id=upload.owner_user_id,
            team_id=upload.team_id,
        )


def _marketplace_with_victim_file(
    *,
    filename: str = "secret.txt",
    mime_type: str = "text/plain",
):
    file_service = FileService(
        uow_factory=lambda: _FileUow(),
        file_storage=_VictimFileStorage(filename=filename, mime_type=mime_type),
    )
    return MarketplaceService(
        llm_model_service=FailingLLMModelService(),
        file_service=file_service,
        uow_factory=fake_uow_factory,
    )


@pytest.mark.anyio
async def test_translate_rejects_file_outside_owner_scope(monkeypatch):
    scope = OwnerScope.personal("attacker-user")
    marketplace = _marketplace_with_victim_file()
    monkeypatch.setattr(
        marketplace,
        "_resolve_text_llm",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        marketplace,
        "_invoke_text",
        AsyncMock(
            return_value=(
                '{"detected_language":"English",'
                '"translated_text":"机密","notes":[]}'
            )
        ),
    )

    with pytest.raises(NotFoundError):
        await marketplace.translate(
            text=None,
            file_id="victim-file",
            target_language="中文",
            style="natural",
            scope=scope,
        )


@pytest.mark.anyio
async def test_analyze_nutrition_rejects_file_outside_owner_scope(monkeypatch):
    marketplace = _marketplace_with_victim_file(
        filename="meal.png",
        mime_type="image/png",
    )
    monkeypatch.setattr(
        marketplace,
        "_resolve_vision_llm",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        marketplace._nutrition,
        "analyze",
        AsyncMock(return_value={}),
    )

    with pytest.raises(NotFoundError):
        await marketplace.analyze_nutrition(
            "victim-file",
            scope=OwnerScope.personal("attacker-user"),
        )


@pytest.mark.anyio
async def test_analyze_consumption_rejects_file_outside_owner_scope(monkeypatch):
    marketplace = _marketplace_with_victim_file(
        filename="label.png",
        mime_type="image/png",
    )
    monkeypatch.setattr(
        marketplace,
        "_resolve_vision_llm",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        marketplace._consumption,
        "analyze_from_image",
        AsyncMock(return_value={}),
    )

    with pytest.raises(NotFoundError):
        await marketplace.analyze_consumption(
            "victim-file",
            50,
            scope=OwnerScope.personal("attacker-user"),
        )


@pytest.mark.anyio
async def test_convert_document_rejects_file_outside_owner_scope(monkeypatch):
    marketplace = _marketplace_with_victim_file()
    monkeypatch.setattr(
        marketplace._conversion,
        "convert",
        lambda *args, **kwargs: (b"converted", "text/plain", "converted.txt"),
    )

    with pytest.raises(NotFoundError):
        await marketplace.convert_document(
            "victim-file",
            "txt",
            scope=OwnerScope.personal("attacker-user"),
        )


@pytest.mark.anyio
async def test_add_watermark_rejects_file_outside_owner_scope(monkeypatch):
    marketplace = _marketplace_with_victim_file(
        filename="victim.png",
        mime_type="image/png",
    )
    monkeypatch.setattr(
        marketplace._watermark,
        "add_image_text_watermark",
        lambda *args, **kwargs: b"watermarked",
    )

    with pytest.raises(NotFoundError):
        await marketplace.add_watermark(
            "victim-file",
            text="safe",
            scope=OwnerScope.personal("attacker-user"),
        )


@pytest.mark.anyio
async def test_remove_watermark_rejects_file_outside_owner_scope(monkeypatch):
    marketplace = _marketplace_with_victim_file(
        filename="victim.pdf",
        mime_type="application/pdf",
    )
    monkeypatch.setattr(
        marketplace._watermark,
        "remove_pdf_watermark",
        lambda *args, **kwargs: b"cleaned",
    )

    with pytest.raises(NotFoundError):
        await marketplace.remove_watermark(
            "victim-file",
            scope=OwnerScope.personal("attacker-user"),
        )


@pytest.mark.anyio
async def test_convert_document_requires_explicit_owner_scope():
    marketplace = _marketplace_with_victim_file()

    with pytest.raises(TypeError):
        await marketplace.convert_document("victim-file", "txt")


@pytest.mark.anyio
async def test_add_watermark_requires_explicit_owner_scope():
    marketplace = _marketplace_with_victim_file(
        filename="victim.png",
        mime_type="image/png",
    )

    with pytest.raises(TypeError):
        await marketplace.add_watermark("victim-file", text="safe")


@pytest.mark.anyio
async def test_remove_watermark_requires_explicit_owner_scope():
    marketplace = _marketplace_with_victim_file(
        filename="victim.pdf",
        mime_type="application/pdf",
    )

    with pytest.raises(TypeError):
        await marketplace.remove_watermark("victim-file")


class _ScopeRecordingModelService:
    def __init__(self):
        self.scope = None

    async def resolve_model(self, model_id=None, *, scope):
        self.scope = scope
        return LLMModel(
            id=model_id or "global-default",
            endpoint_id="endpoint-1",
            display_name="Scoped Model",
            model_name="gpt-test",
        )


@pytest.mark.anyio
async def test_text_model_resolution_requires_owner_scope(monkeypatch):
    model_service = _ScopeRecordingModelService()
    marketplace = MarketplaceService(
        llm_model_service=model_service,
        file_service=UnusedFileService(),
        uow_factory=fake_uow_factory,
    )
    monkeypatch.setattr(
        "app.application.services.marketplace_service.create_resilient_llm",
        lambda model, llm_model_service=None: object(),
    )
    scope = OwnerScope.team("user-1", "team-1")

    await marketplace._resolve_text_llm("model-1", scope=scope)

    assert model_service.scope == scope
