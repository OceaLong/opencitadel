"""Run command/query API; lifecycle state is never directly writable."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.contexts.kernel.runtime import KernelApiRuntime
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.service_dependencies import get_kernel_api_runtime
from app.kernel.application.ports import KernelAuthorization
from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.types import OwnerScopeRef, Workflow
from app.kernel.interfaces.schemas import (
    CancelRunRequest,
    CreateRunRequest,
    DispositionAction,
    DispositionCommandRequest,
    PromptRunRequest,
    RestoreRunRequest,
)

router = APIRouter(prefix="/runs", tags=["runs"])


def _scope(workspace: WorkspaceContext) -> OwnerScopeRef:
    if workspace.scope.team_id:
        return OwnerScopeRef.team(workspace.scope.team_id)
    return OwnerScopeRef.personal(workspace.principal.user_id)


def _authorization(workspace: WorkspaceContext) -> KernelAuthorization:
    return KernelAuthorization(
        actor_user_id=workspace.principal.user_id,
        allowed_scopes=(_scope(workspace),),
        is_admin=workspace.principal.is_admin,
    )


def _ack(result) -> dict[str, object]:
    return {"data": result.model_dump(mode="json", by_alias=True)}


def _command(
    *,
    command_id: UUID | None,
    run_id: UUID,
    workflow: Workflow,
    type_: str,
    payload: dict[str, object],
    expected_stream_version: int | None,
    workspace: WorkspaceContext,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=command_id or uuid4(),
        run_id=run_id,
        workflow=workflow,
        type=type_,
        payload=payload,
        expected_stream_version=expected_stream_version,
        owner_scope=_scope(workspace),
        actor_user_id=workspace.principal.user_id,
        request_id=str(uuid4()),
        submitted_at=datetime.now(UTC),
    )


async def _workflow(runtime: KernelApiRuntime, run_id: UUID, scope: OwnerScopeRef) -> Workflow:
    value = await runtime.queries.get_run(run_id, scope)
    if value is None:
        raise HTTPException(status_code=404, detail={"key": "run.notFound"})
    return Workflow(str(value["workflow"]))


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    body: CreateRunRequest,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KernelApiRuntime = Depends(get_kernel_api_runtime),
):
    run_id = body.run_id or uuid4()
    if runtime.catalog is None:
        raise RuntimeError("kernel tool catalog is not configured")
    tool_catalog = await runtime.catalog.for_scope(_scope(workspace))
    result = await runtime.commands.submit(
        _command(
            command_id=body.command_id,
            run_id=run_id,
            workflow=Workflow.AGENT,
            type_="StartAgent",
            payload={
                "prompt": body.prompt,
                "title": body.title,
                "knowledge_version_ids": body.knowledge_version_ids,
                "tool_catalog": tool_catalog,
            },
            expected_stream_version=0,
            workspace=workspace,
        ),
        _authorization(workspace),
    )
    return _ack(result)


@router.get("")
async def list_runs(
    run_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KernelApiRuntime = Depends(get_kernel_api_runtime),
):
    return {
        "data": await runtime.queries.list_runs(_scope(workspace), status=run_status, limit=limit)
    }


@router.get("/{run_id}")
async def get_run(
    run_id: UUID,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KernelApiRuntime = Depends(get_kernel_api_runtime),
):
    value = await runtime.queries.get_run(run_id, _scope(workspace))
    if value is None:
        raise HTTPException(status_code=404, detail={"key": "run.notFound"})
    return {"data": value}


@router.get("/{run_id}/history")
async def get_run_history(
    run_id: UUID,
    after_version: int = Query(default=0, alias="afterVersion", ge=0),
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KernelApiRuntime = Depends(get_kernel_api_runtime),
):
    return {
        "data": await runtime.queries.history(
            run_id, _scope(workspace), after_version=after_version
        )
    }


@router.get("/{run_id}/events")
async def stream_run_events(
    run_id: UUID,
    after_version: int = Query(default=0, alias="afterVersion", ge=0),
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KernelApiRuntime = Depends(get_kernel_api_runtime),
):
    events = await runtime.queries.history(run_id, _scope(workspace), after_version=after_version)

    async def body():
        for event in events:
            yield f"id: {event['version']}\ndata: {json.dumps(event)}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream")


@router.post("/{run_id}/commands/prompt", status_code=status.HTTP_202_ACCEPTED)
async def submit_prompt(
    run_id: UUID,
    body: PromptRunRequest,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KernelApiRuntime = Depends(get_kernel_api_runtime),
):
    workflow = await _workflow(runtime, run_id, _scope(workspace))
    result = await runtime.commands.submit(
        _command(
            command_id=body.command_id,
            run_id=run_id,
            workflow=workflow,
            type_="SubmitPrompt",
            payload={"prompt": body.prompt},
            expected_stream_version=body.expected_stream_version,
            workspace=workspace,
        ),
        _authorization(workspace),
    )
    return _ack(result)


@router.post("/{run_id}/commands/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(
    run_id: UUID,
    body: CancelRunRequest,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KernelApiRuntime = Depends(get_kernel_api_runtime),
):
    workflow = await _workflow(runtime, run_id, _scope(workspace))
    result = await runtime.commands.submit(
        _command(
            command_id=body.command_id,
            run_id=run_id,
            workflow=workflow,
            type_="CancelRun",
            payload={"reason": body.reason},
            expected_stream_version=body.expected_stream_version,
            workspace=workspace,
        ),
        _authorization(workspace),
    )
    return _ack(result)


@router.get("/{run_id}/disposition")
async def get_run_disposition(
    run_id: UUID,
    action: DispositionAction,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KernelApiRuntime = Depends(get_kernel_api_runtime),
):
    return {
        "data": await runtime.dispositions.preview_run(
            run_id, _scope(workspace), action=action.value
        )
    }


async def _submit_disposition(
    *,
    run_id: UUID,
    action: DispositionAction,
    body: DispositionCommandRequest,
    workspace: WorkspaceContext,
    runtime: KernelApiRuntime,
):
    plan = await runtime.dispositions.validate_run(
        run_id,
        _scope(workspace),
        action=action.value,
        plan_hash=body.plan_hash,
        confirmation=body.confirmation,
    )
    if plan is None:
        raise HTTPException(status_code=409, detail={"key": "disposition.stale"})
    workflow = await _workflow(runtime, run_id, _scope(workspace))
    payload = {"plan_hash": body.plan_hash}
    if action is DispositionAction.ARCHIVE:
        payload["purge_after"] = plan["purgeAfter"]
    result = await runtime.commands.submit(
        _command(
            command_id=body.command_id,
            run_id=run_id,
            workflow=workflow,
            type_="ArchiveRun" if action is DispositionAction.ARCHIVE else "PurgeRun",
            payload=payload,
            expected_stream_version=body.expected_stream_version,
            workspace=workspace,
        ),
        _authorization(workspace),
    )
    return _ack(result)


@router.post("/{run_id}/commands/archive", status_code=status.HTTP_202_ACCEPTED)
async def archive_run(
    run_id: UUID,
    body: DispositionCommandRequest,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KernelApiRuntime = Depends(get_kernel_api_runtime),
):
    return await _submit_disposition(
        run_id=run_id,
        action=DispositionAction.ARCHIVE,
        body=body,
        workspace=workspace,
        runtime=runtime,
    )


@router.post("/{run_id}/commands/purge", status_code=status.HTTP_202_ACCEPTED)
async def purge_run(
    run_id: UUID,
    body: DispositionCommandRequest,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KernelApiRuntime = Depends(get_kernel_api_runtime),
):
    return await _submit_disposition(
        run_id=run_id,
        action=DispositionAction.PURGE,
        body=body,
        workspace=workspace,
        runtime=runtime,
    )


@router.post("/{run_id}/commands/restore", status_code=status.HTTP_202_ACCEPTED)
async def restore_run(
    run_id: UUID,
    body: RestoreRunRequest,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KernelApiRuntime = Depends(get_kernel_api_runtime),
):
    workflow = await _workflow(runtime, run_id, _scope(workspace))
    result = await runtime.commands.submit(
        _command(
            command_id=body.command_id,
            run_id=run_id,
            workflow=workflow,
            type_="RestoreRun",
            payload={},
            expected_stream_version=body.expected_stream_version,
            workspace=workspace,
        ),
        _authorization(workspace),
    )
    return _ack(result)
