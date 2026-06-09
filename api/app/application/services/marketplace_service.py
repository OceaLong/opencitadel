#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import asyncio
from typing import Optional

from app.application.services.file_service import FileService
from app.application.services.llm_model_service import LLMModelService
from app.application.services.marketplace.consumption_service import ConsumptionService
from app.application.services.marketplace.nutrition_service import NutritionService
from app.application.services.marketplace.video_search_service import VideoSearchService
from app.infrastructure.external.llm.factory import LLMFactory

logger = logging.getLogger(__name__)

MARKETPLACE_APPS = [
    {
        "id": "video-search",
        "name": "影视资源聚合",
        "description": "聚合正版免费观看入口，支持中文/英文剧名搜索",
        "icon": "🎬",
        "category": "娱乐",
    },
    {
        "id": "nutrition-analysis",
        "name": "AI营养分析",
        "description": "拍照识别餐食营养，减脂/增肌红绿灯评估",
        "icon": "🥗",
        "category": "健康",
    },
    {
        "id": "consumption-calculator",
        "name": "消耗计算器",
        "description": "识别包装净含量，计算可食用次数",
        "icon": "📦",
        "category": "生活",
    },
]


class MarketplaceService:
    def __init__(
            self,
            llm_model_service: LLMModelService,
            file_service: FileService,
    ) -> None:
        self._llm_model_service = llm_model_service
        self._file_service = file_service
        self._video = VideoSearchService()
        self._nutrition = NutritionService()
        self._consumption = ConsumptionService()

    def list_apps(self) -> list[dict]:
        return MARKETPLACE_APPS

    async def search_videos(self, query: str) -> dict:
        return await self._video.search(query)

    async def analyze_nutrition(
            self,
            file_id: str,
            *,
            model_id: Optional[str] = None,
            weight_kg: Optional[float] = None,
            goal: Optional[str] = None,
    ) -> dict:
        image_bytes, file_info = await self._load_image(file_id)
        llm = await self._resolve_vision_llm(model_id)
        return await self._nutrition.analyze(
            llm,
            image_bytes,
            file_info.mime_type,
            weight_kg=weight_kg,
            goal=goal,
        )

    async def analyze_consumption(
            self,
            file_id: str,
            serving_grams: float,
            *,
            model_id: Optional[str] = None,
    ) -> dict:
        image_bytes, file_info = await self._load_image(file_id)
        llm = await self._resolve_vision_llm(model_id)
        return await self._consumption.analyze_from_image(
            llm, image_bytes, file_info.mime_type, serving_grams,
        )

    def calculate_consumption_manual(self, total_grams: float, serving_grams: float) -> dict:
        return self._consumption.calculate_manual(total_grams, serving_grams)

    async def _load_image(self, file_id: str):
        file_data, file_info = await self._file_service.download_file(file_id)
        image_bytes = await asyncio.to_thread(file_data.read)
        return image_bytes, file_info

    async def _resolve_vision_llm(self, model_id: Optional[str]):
        model = await self._llm_model_service.resolve_model(model_id)
        if not model.capabilities.vision and not model.supports_multimodal:
            raise ValueError("请选择支持多模态能力的模型，或在模型设置中开启视觉能力")
        return LLMFactory.create(model)
