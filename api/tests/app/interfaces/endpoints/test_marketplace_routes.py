#!/usr/bin/env python
# -*- coding: utf-8 -*-


def test_list_marketplace_apps(client):
    response = client.get("/api/marketplace/apps")
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200
    apps = payload["data"]["apps"]
    assert len(apps) == 3
    assert apps[0]["id"] in {"video-search", "nutrition-analysis", "consumption-calculator"}


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
