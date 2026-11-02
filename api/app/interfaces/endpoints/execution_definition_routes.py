"""Global administrator definition inventory, publication and drain control."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from fastapi import Response as HTTPResponse
from pydantic import BaseModel, ConfigDict, Field

from app.application.execution.definition_management import DefinitionChange
from app.domain.execution.definition import Digest
from app.domain.execution.family import RunFamily
from app.interfaces.auth_dependencies import get_workspace_context, require_admin
from app.interfaces.schemas import Response
from app.interfaces.schemas.execution_definitions import (
    DefinitionPage,
    DefinitionRequestResult,
    WorkerPage,
)
from app.interfaces.service_dependencies import get_definition_management_service

router = APIRouter(
    prefix="/admin", tags=["Execution Definitions"], dependencies=[Depends(require_admin)]
)


class HeadChange(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    expected_digest: Digest
    target_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class Retirement(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    expected_manifest_digest: Digest
    reason: str = Field(min_length=1, max_length=2000)


def response(value, http):
    http.headers["Cache-Control"] = "no-store"
    if value.get("status") == "pending":
        http.status_code = 202
        http.headers["Retry-After"] = "1"
    return Response.success(data=value)


@router.get("/execution-definitions", response_model=Response[DefinitionPage])
async def definitions(
    http: HTTPResponse,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    service=Depends(get_definition_management_service),
):
    return response(await service.definitions(limit=limit, offset=offset), http)


@router.get("/execution-workers", response_model=Response[WorkerPage])
async def workers(
    http: HTTPResponse,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    service=Depends(get_definition_management_service),
):
    return response(await service.workers(limit=limit, offset=offset), http)


@router.post(
    "/execution-definitions/{family}/head", response_model=Response[DefinitionRequestResult]
)
async def head(
    family: RunFamily,
    body: HeadChange,
    http: HTTPResponse,
    ctx=Depends(get_workspace_context),
    service=Depends(get_definition_management_service),
):
    value = DefinitionChange(**body.model_dump(), family=family, kind="head")
    return response(await service.change(ctx.principal, value), http)


@router.post(
    "/execution-definitions/{family}/{version}/retire",
    response_model=Response[DefinitionRequestResult],
)
async def retire(
    family: RunFamily,
    version: Annotated[int, Path(ge=1)],
    body: Retirement,
    http: HTTPResponse,
    ctx=Depends(get_workspace_context),
    service=Depends(get_definition_management_service),
):
    value = DefinitionChange(
        request_id=body.request_id,
        family=family,
        kind="retire",
        target_version=version,
        expected_digest=body.expected_manifest_digest,
        reason=body.reason,
    )
    return response(await service.change(ctx.principal, value), http)


@router.get(
    "/execution-definition-requests/{request_id}", response_model=Response[DefinitionRequestResult]
)
async def result(
    request_id: UUID, http: HTTPResponse, service=Depends(get_definition_management_service)
):
    return response(await service.result(request_id), http)
