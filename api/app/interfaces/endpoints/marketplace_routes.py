#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging

from fastapi import APIRouter, Depends

from app.application.errors.exceptions import BadRequestError
from app.application.services.marketplace_service import MarketplaceService
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.marketplace import (
    ConsumptionAnalysisRequest,
    ConsumptionAnalysisResponse,
    ConsumptionManualRequest,
    MarketplaceAppsResponse,
    MarketplaceAppResponse,
    NutritionAnalysisRequest,
    NutritionAnalysisResponse,
    VideoSearchRequest,
    VideoSearchResponse,
)
from app.interfaces.service_dependencies import get_marketplace_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/marketplace", tags=["应用市场"])


@router.get("/apps", response_model=Response[MarketplaceAppsResponse])
async def list_apps(
        marketplace_service: MarketplaceService = Depends(get_marketplace_service),
) -> Response[MarketplaceAppsResponse]:
    apps = [MarketplaceAppResponse(**app) for app in marketplace_service.list_apps()]
    return Response.success(data=MarketplaceAppsResponse(apps=apps))


@router.post("/video/search", response_model=Response[VideoSearchResponse])
async def search_videos(
        request: VideoSearchRequest,
        marketplace_service: MarketplaceService = Depends(get_marketplace_service),
) -> Response[VideoSearchResponse]:
    try:
        data = await marketplace_service.search_videos(request.query)
        return Response.success(data=VideoSearchResponse(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/nutrition/analyze", response_model=Response[NutritionAnalysisResponse])
async def analyze_nutrition(
        request: NutritionAnalysisRequest,
        marketplace_service: MarketplaceService = Depends(get_marketplace_service),
) -> Response[NutritionAnalysisResponse]:
    try:
        data = await marketplace_service.analyze_nutrition(
            request.file_id,
            model_id=request.model_id,
            weight_kg=request.weight_kg,
            goal=request.goal,
        )
        return Response.success(data=NutritionAnalysisResponse(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/consumption/analyze", response_model=Response[ConsumptionAnalysisResponse])
async def analyze_consumption(
        request: ConsumptionAnalysisRequest,
        marketplace_service: MarketplaceService = Depends(get_marketplace_service),
) -> Response[ConsumptionAnalysisResponse]:
    try:
        data = await marketplace_service.analyze_consumption(
            request.file_id,
            request.serving_grams,
            model_id=request.model_id,
        )
        return Response.success(data=ConsumptionAnalysisResponse(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/consumption/calculate", response_model=Response[ConsumptionAnalysisResponse])
async def calculate_consumption(
        request: ConsumptionManualRequest,
        marketplace_service: MarketplaceService = Depends(get_marketplace_service),
) -> Response[ConsumptionAnalysisResponse]:
    try:
        data = marketplace_service.calculate_consumption_manual(
            request.total_grams,
            request.serving_grams,
        )
        return Response.success(data=ConsumptionAnalysisResponse(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
