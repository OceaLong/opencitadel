"""Scoped configuration/rubric/suite CRUD and immutable publication, without admission."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.application.evaluation.discovery import BuiltinToolChoice
from app.application.evaluation.environment_authority import EnvironmentPreflightAuthority
from app.application.evaluation.preflight import PreflightResult, PreflightService
from app.application.evaluation.recording_authority import RecordingPreflightAuthority
from app.application.evaluation.suite_service import ConfigurationPage, SuiteService
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.evaluation_dataset_routes import DatasetRoute, authorize
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.evaluation_configuration import (
    CreateConfigurationRequest,
    PreflightRequest,
    PublicConfigurationDraft,
    PublicVersion,
    PublishConfigurationRequest,
    UpdateConfigurationRequest,
    public_version,
)
from app.interfaces.schemas.execution_view import READ_RESPONSES
from app.interfaces.service_dependencies import get_environment_service, get_suite_service

Collection = Literal["configs", "rubrics", "suites"]
router = APIRouter(
    prefix="/evaluation",
    tags=["Evaluation configurations"],
    route_class=DatasetRoute,
    responses=READ_RESPONSES,
)


@router.get("/config-options/tools", response_model=Response[list[BuiltinToolChoice]])
async def builtin_choices(
    mode: Literal["ask", "agent"] = "agent",
    skill_id: str | None = None,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: SuiteService = Depends(get_suite_service),
):
    authorize(ctx)
    return Response(
        data=await service.builtin_choices(ctx.scope, ctx.principal, mode=mode, skill_id=skill_id)
    )


@router.post("/batches/preflight", response_model=Response[PreflightResult])
async def preflight(
    payload: PreflightRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: SuiteService = Depends(get_suite_service),
    environments=Depends(get_environment_service),
):
    authorize(ctx, True)
    return Response(
        data=await PreflightService(
            service,
            ctx.principal,
            recordings=RecordingPreflightAuthority(),
            environments=EnvironmentPreflightAuthority(
                environments.registry, ceiling=environments.ceiling
            ),
        ).check(ctx.scope, payload.suite_version)
    )


@router.get("/{collection}/versions", response_model=Response[ConfigurationPage])
async def list_versions(
    collection: Collection,
    entity_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: SuiteService = Depends(get_suite_service),
):
    authorize(ctx)
    return Response(
        data=await service.list(
            ctx.scope,
            ctx.principal,
            collection[:-1],
            versions=True,
            cursor=cursor,
            limit=limit,
            entity_id=entity_id,
        )
    )


@router.get("/{collection}/versions/{version_id}", response_model=Response[PublicVersion])
async def get_version(
    collection: Collection,
    version_id: UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: SuiteService = Depends(get_suite_service),
):
    authorize(ctx)
    return Response(
        data=public_version(
            await service.get_version(ctx.scope, ctx.principal, collection[:-1], version_id)
        )
    )


@router.get("/{collection}", response_model=Response[ConfigurationPage])
async def list_drafts(
    collection: Collection,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: SuiteService = Depends(get_suite_service),
):
    authorize(ctx)
    return Response(
        data=await service.list(
            ctx.scope, ctx.principal, collection[:-1], cursor=cursor, limit=limit
        )
    )


@router.post("/{collection}", response_model=Response[PublicConfigurationDraft])
async def create(
    collection: Collection,
    payload: CreateConfigurationRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: SuiteService = Depends(get_suite_service),
):
    authorize(ctx, True)
    return Response(
        data=await service.create(
            ctx.scope, ctx.principal, kind=collection[:-1], **payload.model_dump(mode="json")
        )
    )


@router.get("/{collection}/{entity_id}", response_model=Response[PublicConfigurationDraft])
async def get_draft(
    collection: Collection,
    entity_id: UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: SuiteService = Depends(get_suite_service),
):
    authorize(ctx)
    return Response(
        data=await service.get_draft(ctx.scope, ctx.principal, collection[:-1], entity_id)
    )


@router.patch("/{collection}/{entity_id}", response_model=Response[PublicConfigurationDraft])
async def update(
    collection: Collection,
    entity_id: UUID,
    payload: UpdateConfigurationRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: SuiteService = Depends(get_suite_service),
):
    authorize(ctx, True)
    return Response(
        data=await service.update(
            ctx.scope,
            ctx.principal,
            kind=collection[:-1],
            entity_id=entity_id,
            **payload.model_dump(mode="json"),
        )
    )


@router.delete("/{collection}/{entity_id}", response_model=Response[PublicConfigurationDraft])
async def delete(
    collection: Collection,
    entity_id: UUID,
    payload: PublishConfigurationRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: SuiteService = Depends(get_suite_service),
):
    authorize(ctx, True)
    return Response(
        data=await service.delete(
            ctx.scope,
            ctx.principal,
            kind=collection[:-1],
            entity_id=entity_id,
            **payload.model_dump(),
        )
    )


@router.post("/{collection}/{entity_id}/publish", response_model=Response[PublicVersion])
async def publish(
    collection: Collection,
    entity_id: UUID,
    payload: PublishConfigurationRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: SuiteService = Depends(get_suite_service),
):
    authorize(ctx, True)
    return Response(
        data=public_version(
            await service.publish(
                ctx.scope,
                ctx.principal,
                kind=collection[:-1],
                entity_id=entity_id,
                **payload.model_dump(),
            )
        )
    )
