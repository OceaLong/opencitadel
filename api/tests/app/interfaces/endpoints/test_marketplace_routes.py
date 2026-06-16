#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.interfaces.service_dependencies import get_marketplace_service
from app.main import app


def test_list_marketplace_apps(client):
    response = client.get("/api/marketplace/apps")
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200
    apps = payload["data"]["apps"]
    assert len(apps) >= 5
    assert apps[0]["id"] in {"video-search", "nutrition-analysis", "consumption-calculator"}
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


def test_predict_fortune_requires_question(client):
    response = client.post(
        "/api/marketplace/fortune/predict",
        json={"mode": "fortune", "question": ""},
    )
    assert response.status_code == 422


def test_get_fortune_share_not_found(client):
    response = client.get("/api/marketplace/fortune/share/nonexistent-id")
    assert response.status_code == 400


def test_route_marketplace_request_contract(client):
    class FakeMarketplaceService:
        async def route_request(self, query, *, model_id=None):
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


def test_search_videos_contract(client):
    class FakeMarketplaceService:
        async def search_videos(self, query, *, analyze_content=False, model_id=None):
            assert query == "三体"
            return {
                "query": query,
                "copyright_notice": "推荐正版资源，请支持版权",
                "results": [
                    {
                        "title": "三体 - 哔哩哔哩站内搜索",
                        "platform": "哔哩哔哩",
                        "icon": "📺",
                        "url": "https://search.bilibili.com/all?keyword=三体",
                        "quality": "以平台页面为准",
                        "condition": "免费/会员",
                        "trust_score": 0.95,
                        "source_type": "platform_search",
                        "recommendation_reason": "正版来源优先推荐",
                    }
                ],
                "stats": {
                    "crawled_candidates": 0,
                    "filtered_risk_sources": 0,
                    "legal_results": 1,
                },
            }

    app.dependency_overrides[get_marketplace_service] = lambda: FakeMarketplaceService()
    try:
        response = client.post(
            "/api/marketplace/video/search",
            json={"query": "三体"},
        )
    finally:
        app.dependency_overrides.pop(get_marketplace_service, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200
    data = payload["data"]
    assert data["query"] == "三体"
    assert len(data["results"]) == 1
    assert data["results"][0]["platform"] == "哔哩哔哩"


def test_search_videos_requires_query(client):
    response = client.post("/api/marketplace/video/search", json={"query": ""})
    assert response.status_code == 422
