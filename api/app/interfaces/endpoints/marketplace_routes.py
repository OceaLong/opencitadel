#!/usr/bin/env python
# -*- coding: utf-8 -*-
from fastapi import APIRouter, Depends

from app.application.errors.exceptions import BadRequestError
from app.application.services.marketplace_service import MarketplaceService
from app.interfaces.auth_dependencies import get_current_principal, get_workspace_context
from app.domain.models.scope import WorkspaceContext
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.marketplace import (
    ConsumptionAnalysisRequest,
    ConsumptionAnalysisResponse,
    ConsumptionCorrectionRequest,
    ConsumptionManualRequest,
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
    DocumentConvertRequest,
    DocumentConvertResponse,
    WatermarkAddRequest,
    WatermarkRemoveRequest,
    WatermarkResultResponse,
)
from app.interfaces.service_dependencies import get_marketplace_service

router = APIRouter(prefix="/marketplace", tags=["应用市场"])


@router.get("/apps", response_model=Response[MarketplaceAppsResponse])
async def list_apps(
        marketplace_service: MarketplaceService = Depends(get_marketplace_service),
) -> Response[MarketplaceAppsResponse]:
    apps = [MarketplaceAppResponse(**app) for app in marketplace_service.list_apps()]
    return Response.success(data=MarketplaceAppsResponse(apps=apps))


@router.post("/assistant/route", response_model=Response[MarketplaceRouteResponse])
async def route_marketplace_request(
        request: MarketplaceRouteRequest,
        _principal=Depends(get_current_principal),
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
        _principal=Depends(get_current_principal),
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
        _principal=Depends(get_current_principal),
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
        _principal=Depends(get_current_principal),
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
        _principal=Depends(get_current_principal),
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
        _principal=Depends(get_current_principal),
        marketplace_service: MarketplaceService = Depends(get_marketplace_service),
) -> Response[ConsumptionAnalysisResponse]:
    try:
        data = marketplace_service.correct_consumption(request.text, request.serving_grams)
        return Response.success(data=ConsumptionAnalysisResponse(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/translation/translate", response_model=Response[TranslationResponse])
async def translate(
        request: TranslationRequest,
        _principal=Depends(get_current_principal),
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


@router.post("/convert", response_model=Response[DocumentConvertResponse])
async def convert_document(
        request: DocumentConvertRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        marketplace_service: MarketplaceService = Depends(get_marketplace_service),
) -> Response[DocumentConvertResponse]:
    try:
        data = await marketplace_service.convert_document(
            request.file_id,
            request.target_format,
            scope=ctx.scope,
        )
        return Response.success(data=DocumentConvertResponse(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/watermark/add", response_model=Response[WatermarkResultResponse])
async def add_watermark(
        request: WatermarkAddRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        marketplace_service: MarketplaceService = Depends(get_marketplace_service),
) -> Response[WatermarkResultResponse]:
    try:
        data = await marketplace_service.add_watermark(
            request.file_id,
            watermark_type=request.watermark_type,
            text=request.text,
            watermark_file_id=request.watermark_file_id,
            opacity=request.opacity,
            rotation=request.rotation,
            tile=request.tile,
            scope=ctx.scope,
        )
        return Response.success(data=WatermarkResultResponse(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/watermark/remove", response_model=Response[WatermarkResultResponse])
async def remove_watermark(
        request: WatermarkRemoveRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        marketplace_service: MarketplaceService = Depends(get_marketplace_service),
) -> Response[WatermarkResultResponse]:
    try:
        data = await marketplace_service.remove_watermark(
            request.file_id,
            watermark_text=request.watermark_text,
            mode=request.mode,
            model_id=request.model_id,
            scope=ctx.scope,
        )
        return Response.success(data=WatermarkResultResponse(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
