#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Response

from app.application.services.llm_status_service import LLMStatusService
from app.interfaces.schemas import Response as ApiResponse
from app.interfaces.service_dependencies import get_llm_status_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/llm", tags=["模型状态"])


@router.get(
    path="/status",
    response_model=ApiResponse[Dict[str, Any]],
    summary="模型域健康状态",
    description="只读聚合默认模型配置、Embedding 与熔断状态，不触发真实模型调用。",
)
async def get_llm_status(
        response: Response,
        llm_status_service: LLMStatusService = Depends(get_llm_status_service),
) -> ApiResponse:
    data = await llm_status_service.get_status()
    response.headers["Cache-Control"] = "max-age=30"
    return ApiResponse.success(msg="模型状态查询成功", data=data)
