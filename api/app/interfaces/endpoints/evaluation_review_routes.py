"""Ordinary authorized reviewers issue bounded commands; auditors remain read-only."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.application.services.capability_service import execution_grants
from app.domain.evaluation.judge_protocol import RescoreRequest
from app.domain.evaluation.review import (
    CurrentReviewContext,
    HumanReview,
    ReviewPage,
    ReviewReceipt,
    ScoreHistoryPage,
)
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.evaluation_dataset_routes import DatasetRoute
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.evaluation_review import (
    AppendHumanReview,
    CancelJudgeCommand,
    RescoreCommand,
)
from app.interfaces.schemas.execution_view import READ_RESPONSES
from app.interfaces.service_dependencies import get_review_service

router = APIRouter(
    prefix="/evaluation",
    tags=["Evaluation reviews"],
    route_class=DatasetRoute,
    responses=READ_RESPONSES,
)


def authorize(ctx, write=False):
    if ("evaluation.review" if write else "evaluation.read") not in execution_grants(ctx):
        raise PermissionError("evaluation permission denied")


@router.get("/reviews", response_model=Response[ReviewPage])
async def list_pending(
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    status: Literal["pending", "complete", "not_required", "all"] = "pending",
    rubric_id: UUID | None = None,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_review_service),
):
    authorize(ctx)
    return Response(
        data=await service.list_pending(
            ctx.scope, cursor, limit, principal=ctx.principal, status=status, rubric_id=rubric_id
        )
    )


@router.get("/results/{result_id}/review-context", response_model=Response[CurrentReviewContext])
async def current_review_context(
    result_id: UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_review_service),
):
    authorize(ctx)
    return Response(data=await service.current_context(ctx.scope, ctx.principal, result_id))


@router.post("/results/{result_id}/scores", response_model=Response[ReviewReceipt])
async def append_score(
    result_id: UUID,
    payload: AppendHumanReview,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_review_service),
):
    authorize(ctx, True)
    return Response(
        data=await service.append_score(
            ctx.scope,
            ctx.principal,
            result_id,
            payload.expected_revision,
            payload.request_id,
            HumanReview.model_validate(
                payload.model_dump(exclude={"request_id", "expected_revision"})
            ),
        )
    )


@router.get("/results/{result_id}/scores", response_model=Response[ScoreHistoryPage])
async def history(
    result_id: UUID,
    evaluation_revision: int | None = Query(None, ge=0),
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_review_service),
):
    authorize(ctx)
    return Response(
        data=await service.history_page(
            ctx.scope,
            ctx.principal,
            result_id,
            evaluation_revision=evaluation_revision,
            cursor=cursor,
            limit=limit,
        )
    )


@router.post(
    "/results/{result_id}/commands/rescore", status_code=202, response_model=Response[ReviewReceipt]
)
async def rescore(
    result_id: UUID,
    payload: RescoreCommand,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_review_service),
):
    authorize(ctx, True)
    return Response(
        code=202,
        data=await service.rescore(
            ctx.scope,
            ctx.principal,
            result_id,
            RescoreRequest.model_validate(payload.model_dump(exclude={"request_id"})),
            payload.request_id,
        ),
    )


@router.post(
    "/results/{result_id}/commands/cancel-judge",
    status_code=202,
    response_model=Response[ReviewReceipt],
)
async def cancel_judge(
    result_id: UUID,
    payload: CancelJudgeCommand,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_review_service),
):
    authorize(ctx, True)
    return Response(
        code=202,
        data=await service.cancel(
            ctx.scope,
            ctx.principal,
            result_id,
            payload.judge_run_id,
            payload.expected_revision,
            payload.expected_result_revision,
            payload.request_id,
        ),
    )


@router.get("/reviews/commands/{command_id}", response_model=Response[ReviewReceipt])
async def command(
    command_id: UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_review_service),
):
    authorize(ctx)
    return Response(data=await service.get_command(ctx.scope, ctx.principal, command_id))
