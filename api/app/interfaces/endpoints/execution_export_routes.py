"""Authenticated export status and verified single-file content, without object URLs."""

from uuid import UUID

from fastapi import APIRouter, Depends
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.application.services.execution_export_download import ExportDownloader
from app.domain.errors import AppException
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.execution_comparison_routes import authorize
from app.interfaces.endpoints.execution_view_routes import ExecutionReadRoute
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.execution_export import EXPORT_RESPONSES, ExportCreate, ExportJob
from app.interfaces.service_dependencies import get_execution_export_service


class ExportRoute(ExecutionReadRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request):
            try:
                return await handler(request)
            except ValueError as error:
                reason = str(error)
                status = (
                    404
                    if "not_found" in reason
                    else 429
                    if "quota" in reason
                    else 410
                    if reason == "export_expired"
                    else 409
                    if any(
                        part in reason
                        for part in (
                            "conflict",
                            "unavailable",
                            "invalidated",
                            "lease_lost",
                            "not_ready",
                            "refresh_required",
                            "authorization_changed",
                        )
                    )
                    else 413
                    if "capacity" in reason
                    else 400
                )
                code = (
                    reason
                    if reason.startswith(("export_", "summary_", "comparison_", "invalid_"))
                    else "invalid_export_request"
                )
                key = (
                    "not_found"
                    if status == 404
                    else "invalid_argument"
                    if status == 400
                    else "resource_unavailable"
                )
                raise AppException(
                    code=status,
                    status_code=status,
                    msg=code,
                    error_key="executionErrors." + key,
                    data={"code": code},
                ) from error

        return wrapped


router = APIRouter(
    prefix="/execution-analysis/exports",
    tags=["Execution exports"],
    route_class=ExportRoute,
    responses=EXPORT_RESPONSES,
)


@router.post("", response_model=Response[ExportJob], status_code=202)
async def create_export(
    payload: ExportCreate,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_execution_export_service),
):
    authorize(ctx)
    return Response(
        code=202,
        data=await service.create(
            ctx.scope, ctx.principal, payload.model_dump(mode="json", exclude_none=True)
        ),
    )


@router.get("/{export_id}", response_model=Response[ExportJob], response_model_exclude_none=True)
async def get_export(
    export_id: UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_execution_export_service),
):
    authorize(ctx)
    return Response(data=await service.get(ctx.scope, ctx.principal, str(export_id)))


@router.get("/{export_id}/content", response_class=StreamingResponse)
async def download_export(
    export_id: UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_execution_export_service),
):
    authorize(ctx)
    spool, encoding, size = await service.download(ctx.scope, ctx.principal, str(export_id))
    return StreamingResponse(
        ExportDownloader.stream(spool),
        media_type="text/csv; charset=utf-8" if encoding == "csv" else "application/json",
        headers={
            "Cache-Control": "no-store",
            "Content-Length": str(size),
            "Content-Disposition": f'attachment; filename="execution-export-{export_id}.{encoding}"',
        },
        background=BackgroundTask(spool.close),
    )
