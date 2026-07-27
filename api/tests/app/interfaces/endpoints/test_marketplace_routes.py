#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.interfaces.endpoints.marketplace_routes import (
    analyze_consumption,
    analyze_nutrition,
    answer_nutrition_followup,
    route_marketplace_request,
    translate,
)
from app.interfaces.schemas.marketplace import (
    ConsumptionAnalysisRequest,
    MarketplaceRouteRequest,
    NutritionAnalysisRequest,
    NutritionAnalysisResponse,
    NutritionFollowupRequest,
    TranslationRequest,
)
from app.interfaces.service_dependencies import get_marketplace_service
from app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_list_marketplace_apps(client):
    response = client.get("/api/marketplace/apps")
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200
    apps = payload["data"]["apps"]
    assert len(apps) >= 5
    assert apps[0]["id"] in {"nutrition-analysis", "consumption-calculator", "smart-translation"}
    assert {"tags", "featured", "accent", "needs_vision", "examples"}.issubset(apps[0])


def test_calculate_consumption_manual(client):
    response = client.post(
        "/api/marketplace/consumption/calculate",
        json={"total_grams": 1000, "serving_grams": 50},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200
    data = payload["data"]
    assert data["recognized"] is True
    assert data["full_servings"] == 20
    assert "20 次" in data["message"]


def test_correct_consumption_from_natural_language(client):
    response = client.post(
        "/api/marketplace/consumption/correct",
        json={"text": "其实净含量是 1.2kg", "serving_grams": 60},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200
    data = payload["data"]
    assert data["recognized"] is True
    assert data["total_grams"] == 1200
    assert data["full_servings"] == 20


def test_route_marketplace_request_contract(client):
    class FakeMarketplaceService:
        async def route_request(self, query, *, model_id=None, scope):
            assert query == "帮我翻译这段英文"
            return {
                "app_id": "smart-translation",
                "confidence": 0.91,
                "reason": "适合使用智能翻译",
                "params": {"target_language": "中文"},
                "suggestions": ["粘贴文本后翻译"],
            }

    app.dependency_overrides[get_marketplace_service] = lambda: FakeMarketplaceService()
    try:
        response = client.post(
            "/api/marketplace/assistant/route",
            json={"query": "帮我翻译这段英文"},
        )
    finally:
        app.dependency_overrides.pop(get_marketplace_service, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200
    data = payload["data"]
    assert data["app_id"] == "smart-translation"
    assert data["params"]["target_language"] == "中文"


class _ScopeRecordingMarketplaceService:
    def __init__(self):
        self.scopes = {}

    async def route_request(self, query, *, model_id=None, scope):
        self.scopes["route"] = scope
        return {
            "app_id": "smart-translation",
            "confidence": 0.91,
            "reason": "scope checked",
            "params": {},
            "suggestions": [],
        }

    async def analyze_nutrition(
        self,
        file_id,
        *,
        model_id=None,
        weight_kg=None,
        goal=None,
        scope,
    ):
        self.scopes["nutrition"] = scope
        return _nutrition_payload()

    async def answer_nutrition_followup(
        self,
        analysis,
        question,
        *,
        model_id=None,
        scope,
    ):
        self.scopes["followup"] = scope
        return {"answer": "scope checked"}

    async def analyze_consumption(
        self,
        file_id,
        serving_grams,
        *,
        model_id=None,
        scope,
    ):
        self.scopes["consumption"] = scope
        return {
            "recognized": True,
            "confidence": 1.0,
            "total_grams": 100,
            "serving_grams": serving_grams,
            "servings": 2,
            "full_servings": 2,
            "message": "scope checked",
        }

    async def translate(
        self,
        *,
        text,
        file_id,
        target_language,
        style,
        model_id=None,
        scope,
    ):
        self.scopes["translation"] = scope
        return {
            "detected_language": "English",
            "target_language": target_language,
            "translated_text": "已检查作用域",
            "notes": [],
        }


def _workspace_context():
    return WorkspaceContext(
        principal=Principal(user_id="user-1"),
        scope=OwnerScope.personal("user-1"),
    )


def _nutrition_payload():
    return {
        "meal_summary": "scope checked",
        "items": [],
        "totals": {
            "calories": 0,
            "protein": 0,
            "fat": 0,
            "carbs": 0,
        },
        "assessment": {
            "overall": "green",
            "lights": {},
            "tips": [],
            "ratios": {},
        },
    }


@pytest.mark.anyio
async def test_route_request_passes_workspace_scope():
    service = _ScopeRecordingMarketplaceService()
    ctx = _workspace_context()

    await route_marketplace_request(
        MarketplaceRouteRequest(query="translate"),
        ctx=ctx,
        marketplace_service=service,
    )

    assert service.scopes["route"] == ctx.scope


@pytest.mark.anyio
async def test_nutrition_analysis_passes_workspace_scope():
    service = _ScopeRecordingMarketplaceService()
    ctx = _workspace_context()

    await analyze_nutrition(
        NutritionAnalysisRequest(file_id="file-1"),
        ctx=ctx,
        marketplace_service=service,
    )

    assert service.scopes["nutrition"] == ctx.scope


@pytest.mark.anyio
async def test_nutrition_followup_passes_workspace_scope():
    service = _ScopeRecordingMarketplaceService()
    ctx = _workspace_context()
    analysis = NutritionAnalysisResponse(**_nutrition_payload())

    await answer_nutrition_followup(
        NutritionFollowupRequest(analysis=analysis, question="next"),
        ctx=ctx,
        marketplace_service=service,
    )

    assert service.scopes["followup"] == ctx.scope


@pytest.mark.anyio
async def test_consumption_analysis_passes_workspace_scope():
    service = _ScopeRecordingMarketplaceService()
    ctx = _workspace_context()

    await analyze_consumption(
        ConsumptionAnalysisRequest(file_id="file-1", serving_grams=50),
        ctx=ctx,
        marketplace_service=service,
    )

    assert service.scopes["consumption"] == ctx.scope


@pytest.mark.anyio
async def test_translation_passes_workspace_scope():
    service = _ScopeRecordingMarketplaceService()
    ctx = _workspace_context()

    await translate(
        TranslationRequest(text="hello"),
        ctx=ctx,
        marketplace_service=service,
    )

    assert service.scopes["translation"] == ctx.scope
