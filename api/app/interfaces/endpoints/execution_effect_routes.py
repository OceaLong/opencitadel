"""Scope-bound effect evidence reads and durable reconciliation requests."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.domain.errors import AppException, BadRequestError, ForbiddenError
from app.domain.models.scope import OwnerScope, WorkspaceContext
from app.interfaces.auth_dependencies import enforce_auditor_read_only, get_workspace_context
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.execution_effect import (
    EffectAbandonRequest,
    EffectOperationResponse,
    EffectQueryRequest,
    EffectRequestResponse,
    EffectResolutionRequest,
)
from app.interfaces.service_dependencies import get_effect_service


class ExecutionEffectRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request):
            try:
                response = await handler(request)
                response.headers["Cache-Control"] = "no-store"
                response.headers["Vary"] = (
                    "Cookie, Authorization, X-Workspace-Id, X-Effect-Scope-User-Id, "
                    "X-Effect-Scope-Team-Id, X-Effect-Scope-Reason"
                )
                return response
            except RequestValidationError as error:
                # A structurally invalid evidence/attestation body is unprocessable,
                # just like an invalid binding detected by the evidence service.
                status = 422 if any(e["loc"][0] == "body" for e in error.errors()) else 400
                raise AppException(
                    code=status,
                    status_code=status,
                    msg="invalid_argument",
                    data={"code": "invalid_argument"},
                    error_key="executionErrors.invalid_argument",
                ) from error

        return wrapped


router = APIRouter(tags=["Execution effects"], route_class=ExecutionEffectRoute)
_BASE = "/execution-runs/{run_id}/effects"
_WRITE = [Depends(enforce_auditor_read_only)]


async def effect_context(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    user_id: str | None = Header(None, alias="X-Effect-Scope-User-Id", max_length=255),
    team_id: str | None = Header(None, alias="X-Effect-Scope-Team-Id", max_length=255),
    reason: str | None = Header(None, alias="X-Effect-Scope-Reason", max_length=2000),
) -> tuple[WorkspaceContext, str | None]:
    """Cross-scope admin access is explicit, auditable and never inferred from a run ID."""
    if any(value is not None for value in (user_id, team_id, reason)):
        if not ctx.principal.is_admin:
            raise ForbiddenError()
        if not user_id or not user_id.strip() or not reason or not reason.strip():
            raise BadRequestError("Explicit target scope and reason required")
        if team_id is not None and not team_id.strip():
            raise BadRequestError("Invalid target team")
        scope = (
            OwnerScope.team(user_id.strip(), team_id.strip())
            if team_id
            else OwnerScope.personal(user_id.strip())
        )
        return WorkspaceContext(principal=ctx.principal, scope=scope), reason.strip()
    return ctx, None


def _identity(run_id, context, operation_id=None):
    ctx, reason = context
    values = {
        "run_id": run_id,
        "scope": ctx.scope,
        "principal": ctx.principal,
        "scope_reason": reason,
    }
    if operation_id is not None:
        values["operation_id"] = operation_id
    return values


def _key(value: str) -> str:
    if not value.strip():
        raise BadRequestError("Idempotency-Key required")
    return value.strip()


@router.get(_BASE, response_model=Response[list[EffectOperationResponse]])
async def list_effects(
    run_id: UUID,
    context=Depends(effect_context),
    service=Depends(get_effect_service),
):
    return Response.success(await service.list_operations(**_identity(run_id, context)))


@router.get(_BASE + "/{operation_id}", response_model=Response[EffectOperationResponse])
async def get_effect(
    run_id: UUID,
    operation_id: UUID,
    context=Depends(effect_context),
    service=Depends(get_effect_service),
):
    return Response.success(await service.get_operation(**_identity(run_id, context, operation_id)))


@router.post(
    _BASE + "/{operation_id}/queries",
    status_code=202,
    response_model=Response[EffectRequestResponse],
    dependencies=_WRITE,
)
async def query_effect(
    run_id: UUID,
    operation_id: UUID,
    body: EffectQueryRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    context=Depends(effect_context),
    service=Depends(get_effect_service),
):
    return Response(
        code=202,
        data=await service.submit_query(
            **_identity(run_id, context, operation_id),
            **body.model_dump(mode="json"),
            idempotency_key=_key(idempotency_key),
        ),
    )


@router.post(
    _BASE + "/{operation_id}/resolutions",
    status_code=202,
    response_model=Response[EffectRequestResponse],
    dependencies=_WRITE,
)
async def resolve_effect(
    run_id: UUID,
    operation_id: UUID,
    body: EffectResolutionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    context=Depends(effect_context),
    service=Depends(get_effect_service),
):
    return Response(
        code=202,
        data=await service.submit_resolution(
            **_identity(run_id, context, operation_id),
            **body.model_dump(mode="json"),
            idempotency_key=_key(idempotency_key),
        ),
    )


@router.post(
    _BASE + "/{operation_id}/abandon",
    status_code=202,
    response_model=Response[EffectRequestResponse],
    dependencies=_WRITE,
)
async def abandon_effect(
    run_id: UUID,
    operation_id: UUID,
    body: EffectAbandonRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    context=Depends(effect_context),
    service=Depends(get_effect_service),
):
    return Response(
        code=202,
        data=await service.submit_abandon(
            **_identity(run_id, context, operation_id),
            **body.model_dump(mode="json"),
            idempotency_key=_key(idempotency_key),
        ),
    )


@router.get(
    _BASE + "/{operation_id}/requests/{request_id}",
    response_model=Response[EffectRequestResponse],
)
async def get_effect_request(
    run_id: UUID,
    operation_id: UUID,
    request_id: UUID,
    context=Depends(effect_context),
    service=Depends(get_effect_service),
):
    return Response.success(
        await service.get_request(**_identity(run_id, context, operation_id), request_id=request_id)
    )
