#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging

from fastapi import APIRouter, Depends, Header, Query

from app.application.errors.exceptions import BadRequestError
from app.application.services.questionnaire_service import QuestionnaireService
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.questionnaire import (
    CreateQuestionnaireRequest,
    PublishQuestionnaireRequest,
    QuestionnaireResponseSchema,
    QuestionnaireStatsSchema,
    SubmitResponseRequest,
    SubmitResponseResultSchema,
    UpdateQuestionnaireRequest,
)
from app.interfaces.service_dependencies import get_questionnaire_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/marketplace/questionnaires", tags=["问卷"])


def _resolve_manage_token(
        query_token: str | None = None,
        header_token: str | None = None,
        body_token: str | None = None,
) -> str:
    token = (header_token or body_token or query_token or "").strip()
    if not token:
        raise BadRequestError("缺少管理令牌")
    return token


@router.post("", response_model=Response[QuestionnaireResponseSchema])
async def create_questionnaire(
        request: CreateQuestionnaireRequest,
        service: QuestionnaireService = Depends(get_questionnaire_service),
) -> Response[QuestionnaireResponseSchema]:
    try:
        data = await service.create(request.title, request.description, request.questions)
        return Response.success(data=QuestionnaireResponseSchema(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.patch("/{questionnaire_id}", response_model=Response[QuestionnaireResponseSchema])
async def update_questionnaire(
        questionnaire_id: str,
        request: UpdateQuestionnaireRequest,
        service: QuestionnaireService = Depends(get_questionnaire_service),
) -> Response[QuestionnaireResponseSchema]:
    try:
        data = await service.update(
            questionnaire_id,
            request.manage_token,
            title=request.title,
            description=request.description,
            questions=request.questions,
        )
        return Response.success(data=QuestionnaireResponseSchema(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/{questionnaire_id}/publish", response_model=Response[QuestionnaireResponseSchema])
async def publish_questionnaire(
        questionnaire_id: str,
        request: PublishQuestionnaireRequest,
        service: QuestionnaireService = Depends(get_questionnaire_service),
) -> Response[QuestionnaireResponseSchema]:
    try:
        data = await service.publish(questionnaire_id, request.manage_token)
        return Response.success(data=QuestionnaireResponseSchema(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/{questionnaire_id}/close", response_model=Response[QuestionnaireResponseSchema])
async def close_questionnaire(
        questionnaire_id: str,
        request: PublishQuestionnaireRequest,
        service: QuestionnaireService = Depends(get_questionnaire_service),
) -> Response[QuestionnaireResponseSchema]:
    try:
        data = await service.close(questionnaire_id, request.manage_token)
        return Response.success(data=QuestionnaireResponseSchema(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.get("/public/{slug}", response_model=Response[QuestionnaireResponseSchema])
async def get_public_questionnaire(
        slug: str,
        service: QuestionnaireService = Depends(get_questionnaire_service),
) -> Response[QuestionnaireResponseSchema]:
    try:
        data = await service.get_public(slug)
        return Response.success(data=QuestionnaireResponseSchema(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/public/{slug}/responses", response_model=Response[SubmitResponseResultSchema])
async def submit_response(
        slug: str,
        request: SubmitResponseRequest,
        service: QuestionnaireService = Depends(get_questionnaire_service),
) -> Response[SubmitResponseResultSchema]:
    try:
        data = await service.submit_response(slug, request.answers, request.respondent_name)
        return Response.success(data=SubmitResponseResultSchema(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.get("/{questionnaire_id}/stats", response_model=Response[QuestionnaireStatsSchema])
async def get_stats(
        questionnaire_id: str,
        manage_token: str | None = Query(None),
        x_manage_token: str | None = Header(None, alias="X-Manage-Token"),
        service: QuestionnaireService = Depends(get_questionnaire_service),
) -> Response[QuestionnaireStatsSchema]:
    try:
        token = _resolve_manage_token(manage_token, x_manage_token)
        data = await service.get_stats(questionnaire_id, token)
        return Response.success(data=QuestionnaireStatsSchema(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.get("/{questionnaire_id}", response_model=Response[QuestionnaireResponseSchema])
async def get_questionnaire(
        questionnaire_id: str,
        manage_token: str | None = Query(None),
        x_manage_token: str | None = Header(None, alias="X-Manage-Token"),
        service: QuestionnaireService = Depends(get_questionnaire_service),
) -> Response[QuestionnaireResponseSchema]:
    try:
        token = _resolve_manage_token(manage_token, x_manage_token)
        data = await service.get_by_id(questionnaire_id, token)
        return Response.success(data=QuestionnaireResponseSchema(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
