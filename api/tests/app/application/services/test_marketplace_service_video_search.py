#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.services import marketplace_service as marketplace_service_module
from app.application.services.marketplace_service import MarketplaceService


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def marketplace_service(monkeypatch):
    monkeypatch.setattr(
        marketplace_service_module,
        "VIDEO_SEARCH_LLM_RANK_TIMEOUT_SECONDS",
        0.05,
    )
    service = MarketplaceService(
        llm_model_service=MagicMock(),
        file_service=MagicMock(),
        uow_factory=MagicMock(),
    )
    service._video = AsyncMock()
    service._video.search = AsyncMock(
        return_value={
            "query": "三体",
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
                }
            ],
            "stats": {
                "crawled_candidates": 0,
                "filtered_risk_sources": 0,
                "legal_results": 1,
            },
        }
    )
    service._resolve_text_llm = AsyncMock(return_value=MagicMock())

    async def slow_rank(_llm, data):
        await asyncio.sleep(0.2)
        return data

    service._rank_video_results = slow_rank
    return service


@pytest.mark.anyio
async def test_search_videos_returns_unranked_results_on_llm_timeout(marketplace_service):
    data = await marketplace_service.search_videos("三体")

    assert data["query"] == "三体"
    assert len(data["results"]) == 1
    assert data["results"][0]["recommendation_reason"] == "正版来源优先推荐"
