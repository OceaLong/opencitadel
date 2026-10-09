"""Administrative inventory selection; ordinary business reads retain workspace authority."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.application.evaluation.discovery import EnvironmentInventory, EnvironmentPage
from app.application.evaluation.environment_service import EnvironmentService
from app.domain.evaluation.environment import EnvironmentVersion, TestCredentialRef, TestTarget
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.evaluation_dataset_routes import DatasetRoute, authorize
from app.interfaces.schemas.base import Response
from app.interfaces.service_dependencies import get_environment_service

router = APIRouter(
    prefix="/evaluation/environments", tags=["Evaluation environments"], route_class=DatasetRoute
)


class RegisterEnvironmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=255)
    kind: Literal["environment", "target", "credential"]
    value: EnvironmentVersion | TestTarget | TestCredentialRef

    @model_validator(mode="after")
    def matching_kind(self):
        expected = {
            "environment": EnvironmentVersion,
            "target": TestTarget,
            "credential": TestCredentialRef,
        }[self.kind]
        if not isinstance(self.value, expected):
            raise ValueError("environment_registry_kind_mismatch")  # noqa: TRY004 - Pydantic validation
        return self


class RegistryReference(BaseModel):
    id: UUID
    kind: Literal["environment", "target", "credential"]
    revision: int


class InventorySelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=255)
    revision: int = Field(ge=1)


@router.get("/inventory", response_model=Response[EnvironmentInventory])
async def inventory(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: EnvironmentService = Depends(get_environment_service),
):
    authorize(ctx, True)
    return Response(data=await service.inventory(ctx.scope, ctx.principal))


@router.post("/inventory/{kind}/{identity}/register", response_model=Response[RegistryReference])
async def select_inventory(
    kind: Literal["target", "credential"],
    identity: UUID,
    payload: InventorySelectionRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: EnvironmentService = Depends(get_environment_service),
):
    authorize(ctx, True)
    return Response(
        data=await service.register_inventory(
            ctx.scope,
            ctx.principal,
            kind,
            identity,
            payload.revision,
            request_id=payload.request_id,
        )
    )


@router.get("", response_model=Response[EnvironmentPage])
async def list_environments(
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: EnvironmentService = Depends(get_environment_service),
):
    authorize(ctx)
    return Response(
        data=await service.list_versions(ctx.scope, ctx.principal, cursor=cursor, limit=limit)
    )


@router.post("/registry", response_model=Response[RegistryReference])
async def register(
    payload: RegisterEnvironmentRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: EnvironmentService = Depends(get_environment_service),
):
    authorize(ctx, True)
    return Response(
        data=await service.register(
            ctx.scope, ctx.principal, payload.kind, payload.value, request_id=payload.request_id
        )
    )


@router.get("/{version_id}", response_model=Response[EnvironmentVersion])
async def version(
    version_id: UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: EnvironmentService = Depends(get_environment_service),
):
    authorize(ctx)
    return Response(data=await service.version(ctx.scope, ctx.principal, version_id))


class RepairEnvironmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=255)


class RepairReference(BaseModel):
    id: UUID
    lease_id: UUID
    revision: int
    status: Literal["queued"]


@router.post("/leases/{lease_id}/repair", response_model=Response[RepairReference], status_code=202)
async def repair(
    lease_id: UUID,
    payload: RepairEnvironmentRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: EnvironmentService = Depends(get_environment_service),
):
    authorize(ctx, True)
    return Response(
        code=202,
        data=await service.repair(
            ctx.scope, ctx.principal, lease_id, request_id=payload.request_id
        ),
    )
