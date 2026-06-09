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
    ConsumptionCorrectionRequest,
    ConsumptionManualRequest,
    DocumentQaRequest,
    DocumentQaResponse,
    MarketplaceAppsResponse,
    MarketplaceAppResponse,
    MarketplaceRouteRequest,
    MarketplaceRouteResponse,
    NutritionAnalysisRequest,
    NutritionAnalysisResponse,
    NutritionFollowupRequest,
    NutritionFollowupResponse,
    TranslationRequest,
    TranslationResponse,
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


@router.post("/assistant/route", response_model=Response[MarketplaceRouteResponse])
async def route_marketplace_request(
        request: MarketplaceRouteRequest,
        marketplace_service: MarketplaceService = Depends(get_marketplace_service),
) -> Response[MarketplaceRouteResponse]:
    try:
        data = await marketplace_service.route_request(
            request.query,
            model_id=request.model_id,
        )
        return Response.success(data=MarketplaceRouteResponse(**data))
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


@router.post("/nutrition/followup", response_model=Response[NutritionFollowupResponse])
async def answer_nutrition_followup(
        request: NutritionFollowupRequest,
        marketplace_service: MarketplaceService = Depends(get_marketplace_service),
) -> Response[NutritionFollowupResponse]:
    try:
        data = await marketplace_service.answer_nutrition_followup(
            request.analysis.model_dump(),
            request.question,
            model_id=request.model_id,
        )
        return Response.success(data=NutritionFollowupResponse(**data))
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


@router.post("/consumption/correct", response_model=Response[ConsumptionAnalysisResponse])
async def correct_consumption(
        request: ConsumptionCorrectionRequest,
        marketplace_service: MarketplaceService = Depends(get_marketplace_service),
) -> Response[ConsumptionAnalysisResponse]:
    try:
        data = marketplace_service.correct_consumption(request.text, request.serving_grams)
        return Response.success(data=ConsumptionAnalysisResponse(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/document-qa/ask", response_model=Response[DocumentQaResponse])
async def ask_document_question(
        request: DocumentQaRequest,
        marketplace_service: MarketplaceService = Depends(get_marketplace_service),
) -> Response[DocumentQaResponse]:
    try:
        data = await marketplace_service.answer_document_question(
            request.file_id,
            request.question,
            model_id=request.model_id,
        )
        return Response.success(data=DocumentQaResponse(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/translation/translate", response_model=Response[TranslationResponse])
async def translate(
        request: TranslationRequest,
        marketplace_service: MarketplaceService = Depends(get_marketplace_service),
) -> Response[TranslationResponse]:
    try:
        data = await marketplace_service.translate(
            text=request.text,
            file_id=request.file_id,
            target_language=request.target_language,
            style=request.style,
            model_id=request.model_id,
        )
        return Response.success(data=TranslationResponse(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
