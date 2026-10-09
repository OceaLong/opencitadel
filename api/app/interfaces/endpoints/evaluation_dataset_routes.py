"""Workspace-authorized dataset API over the real application service."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.exceptions import RequestValidationError

from app.application.evaluation.dataset_service import (
    DatasetService,
    DatasetVersionPage,
    ImportPreview,
)
from app.application.services.capability_service import execution_grants
from app.domain.evaluation.dataset import CaseRevision, DatasetDraft, DatasetSummary, DatasetVersion
from app.domain.evaluation.errors import (
    DatasetConflict,
    DatasetNotFound,
    DatasetUnavailable,
)
from app.domain.models.resource_pin import ResourceUnavailable
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.execution_view_routes import ExecutionReadRoute, read_error
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.evaluation_dataset import (
    AnalysisCertification,
    ApplyImportRequest,
    CreateDatasetRequest,
    FromRunPreviewRequest,
    FromRunRequest,
    MutationRequest,
    UpdateCaseRequest,
)
from app.interfaces.schemas.execution_view import READ_RESPONSES
from app.interfaces.service_dependencies import get_dataset_service


class DatasetRoute(ExecutionReadRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request):
            try:
                return await handler(request)
            except DatasetNotFound as error:
                raise read_error(404, "not_found") from error
            except DatasetConflict as error:
                raise read_error(409, "revision_conflict") from error
            except (DatasetUnavailable, ResourceUnavailable) as error:
                raise read_error(409, "resource_unavailable") from error
            except (ValueError, RequestValidationError) as error:
                raise read_error(400, "invalid_argument") from error

        return wrapped


router = APIRouter(
    prefix="/evaluation",
    tags=["Evaluation datasets"],
    route_class=DatasetRoute,
    responses=READ_RESPONSES,
)


def authorize(ctx, write=False):
    if ("evaluation.manage" if write else "evaluation.read") not in execution_grants(ctx):
        raise PermissionError("evaluation permission denied")


@router.get("/datasets", response_model=Response[list[DatasetSummary]])
async def list_datasets(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: DatasetService = Depends(get_dataset_service),
):
    authorize(ctx)
    return Response(data=await service.list_drafts(ctx.scope, ctx.principal))


@router.post("/datasets", response_model=Response[DatasetDraft])
async def create_dataset(
    payload: CreateDatasetRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: DatasetService = Depends(get_dataset_service),
):
    authorize(ctx, True)
    return Response(
        data=await service.create_draft(ctx.scope, ctx.principal, **payload.model_dump())
    )


@router.get("/datasets/{dataset_id}", response_model=Response[DatasetDraft])
async def get_dataset(
    dataset_id: UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: DatasetService = Depends(get_dataset_service),
):
    authorize(ctx)
    return Response(data=await service.get_draft(ctx.scope, ctx.principal, dataset_id))


@router.patch("/datasets/{dataset_id}/cases/{case_key}", response_model=Response[DatasetDraft])
async def update_case(
    dataset_id: UUID,
    case_key: str,
    payload: UpdateCaseRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: DatasetService = Depends(get_dataset_service),
):
    authorize(ctx, True)
    return Response(
        data=await service.update_case(
            ctx.scope,
            ctx.principal,
            dataset_id=dataset_id,
            request_id=payload.request_id,
            expected_revision=payload.expected_revision,
            case=CaseRevision(case_key=case_key, **payload.case.model_dump()),
        )
    )


@router.post("/datasets/{dataset_id}/imports/validate", response_model=Response[ImportPreview])
async def validate_import(
    dataset_id: UUID,
    request_id: str = Form(min_length=1, max_length=255),
    expected_revision: int = Form(ge=1),
    file: UploadFile = File(),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: DatasetService = Depends(get_dataset_service),
):
    authorize(ctx, True)
    return Response(
        data=await service.import_validate(
            ctx.scope,
            ctx.principal,
            dataset_id=dataset_id,
            request_id=request_id,
            expected_revision=expected_revision,
            stream=file.file,
            content_type=file.content_type or "application/octet-stream",
        )
    )


@router.post(
    "/datasets/{dataset_id}/imports/{import_id}/apply", response_model=Response[DatasetDraft]
)
async def apply_import(
    dataset_id: UUID,
    import_id: UUID,
    payload: ApplyImportRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: DatasetService = Depends(get_dataset_service),
):
    authorize(ctx, True)
    return Response(
        data=await service.import_apply(
            ctx.scope,
            ctx.principal,
            dataset_id=dataset_id,
            import_id=import_id,
            **payload.model_dump(),
        )
    )


@router.post("/datasets/{dataset_id}/from-run", response_model=Response[DatasetDraft])
async def from_run(
    dataset_id: UUID,
    payload: FromRunRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: DatasetService = Depends(get_dataset_service),
):
    authorize(ctx, True)
    return Response(
        data=await service.create_case_from_run(
            ctx.scope, ctx.principal, dataset_id=dataset_id, **payload.model_dump()
        )
    )


@router.post("/datasets/{dataset_id}/publish", response_model=Response[DatasetVersion])
async def publish_dataset(
    dataset_id: UUID,
    payload: MutationRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: DatasetService = Depends(get_dataset_service),
):
    authorize(ctx, True)
    return Response(
        data=await service.publish(
            ctx.scope, ctx.principal, dataset_id=dataset_id, **payload.model_dump()
        )
    )


@router.get("/dataset-versions/{version_id}", response_model=Response[DatasetVersion])
async def get_version(
    version_id: UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: DatasetService = Depends(get_dataset_service),
):
    authorize(ctx)
    return Response(data=await service.get_version(ctx.scope, ctx.principal, version_id))


@router.get("/datasets/{dataset_id}/versions", response_model=Response[DatasetVersionPage])
async def version_history(
    dataset_id: UUID,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: DatasetService = Depends(get_dataset_service),
):
    authorize(ctx)
    return Response(
        data=await service.list_versions(
            ctx.scope, ctx.principal, dataset_id, cursor=cursor, limit=limit
        )
    )


@router.post("/datasets/{dataset_id}/from-run/preview", response_model=Response[CaseRevision])
async def preview_from_run(
    dataset_id: UUID,
    payload: FromRunPreviewRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: DatasetService = Depends(get_dataset_service),
):
    authorize(ctx, True)
    return Response(
        data=await service.preview_case_from_run(
            ctx.scope, ctx.principal, dataset_id=dataset_id, **payload.model_dump()
        )
    )


@router.post(
    "/dataset-versions/{version_id}/analysis-certification",
    response_model=Response[AnalysisCertification],
)
async def certify_analysis_version(
    version_id: UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: DatasetService = Depends(get_dataset_service),
):
    authorize(ctx)
    return Response(
        data=await service.certify_analysis_version(ctx.scope, ctx.principal, version_id)
    )
