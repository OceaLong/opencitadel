#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.services.marketplace_service import MarketplaceService


class FailingLLMModelService:
    async def resolve_model(self, model_id=None):
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
    route = await service.route_request("帮我翻译这段英文为中文")
    assert route["app_id"] == "smart-translation"
    assert route["confidence"] > 0
    assert "suggestions" in route


def test_correct_consumption_extracts_natural_language_total(service):
    result = service.correct_consumption("其实净含量是 1.2kg", serving_grams=60)
    assert result["recognized"] is True
    assert result["total_grams"] == 1200
    assert result["full_servings"] == 20
