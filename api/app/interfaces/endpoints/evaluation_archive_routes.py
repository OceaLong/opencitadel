"""Typed retention-preserving archive; no arbitrary resource deletion."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.application.evaluation.archive_service import ArchiveService
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.evaluation_dataset_routes import DatasetRoute, authorize
from app.interfaces.schemas.base import Response
from app.interfaces.service_dependencies import get_batch_service

router = APIRouter(
    prefix="/evaluation/archives", tags=["Evaluation archives"], route_class=DatasetRoute
)


class ArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["dataset", "config", "rubric", "suite", "recording", "environment", "batch"]
    resource_id: UUID
    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=255)


class ArchiveReceipt(BaseModel):
    kind: str
    resource_id: UUID
    revision: int
    state: Literal["archived"]
    retained: Literal[True]


@router.post("", response_model=Response[ArchiveReceipt])
async def archive(
    body: ArchiveRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_batch_service),
):
    authorize(ctx, write=True)
    return Response(
        data=await ArchiveService(service.suites.uow_factory).archive(
            ctx.scope,
            ctx.principal,
            kind=body.kind,
            identity=body.resource_id,
            expected_revision=body.expected_revision,
            request_id=body.request_id,
        )
    )


class EvaluationPinOwner(BaseModel):
    owner_kind: Literal["dataset_version", "config_version", "recording_version"]
    owner_id: UUID
    resource_version: str


@router.get("/pins", response_model=Response[list[EvaluationPinOwner]])
async def readable_pins(
    resource_kind: Literal["file", "knowledge_base"],
    resource_id: str,
    resource_version: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_batch_service),
):
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.resource_pin import ResourceIdentity

    authorize(ctx)
    resource = ResourceIdentity(
        resource_kind=resource_kind, resource_id=resource_id, resource_version=resource_version
    )
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(ctx.principal, scope=ctx.scope)
    ) as work:
        await work.evaluation_dataset.authorize(ctx.scope, ctx.principal, write=False)
        await work.resource_pins.resolve(ctx.scope, resource)
        owners = await work.resource_pins.readable_evaluation_owners(ctx.scope, resource)
        return Response(data=owners)
