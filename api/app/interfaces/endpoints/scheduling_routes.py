import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.application.ports.streams import NotificationStreamFactory
from app.application.security.authorization_context import authorization_scope
from app.application.services.notification_service import NotificationService
from app.application.services.scheduled_job_service import ScheduledJobService
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scheduled_job import NotifyChannel
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context, require_non_auditor
from app.interfaces.schemas import Response as ApiResponse
from app.interfaces.schemas.notification import (
    NotificationListResponse,
    NotificationResponse,
)
from app.interfaces.schemas.scheduled_job import (
    CreateScheduledJobRequest,
    CreateScheduledJobResponse,
    ScheduledJobListResponse,
    ScheduledJobResponse,
    UpdateScheduledJobRequest,
    WebhookSecretResponse,
)
from app.interfaces.service_dependencies import (
    get_notification_service,
    get_notification_stream_factory,
    get_scheduled_job_service,
)
from app.interfaces.streaming import finish_snapshot_before_cancellation

logger = logging.getLogger(__name__)

scheduled_router = APIRouter(prefix="/scheduled-jobs", tags=["自动化任务"])
notification_router = APIRouter(prefix="/notifications", tags=["通知"])
webhook_router = APIRouter(tags=["Webhook"])


def _job_response(job) -> ScheduledJobResponse:
    return ScheduledJobResponse.model_validate(
        {
            **job.model_dump(mode="json"),
            "notify_channels": job.notify_channels_dict(),
        }
    )


@scheduled_router.get("", response_model=ApiResponse[ScheduledJobListResponse])
async def list_jobs(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ScheduledJobService = Depends(get_scheduled_job_service),
):
    jobs = await service.list_jobs(ctx.scope)
    return ApiResponse.success(ScheduledJobListResponse(jobs=[_job_response(j) for j in jobs]))


@scheduled_router.post("", response_model=ApiResponse[CreateScheduledJobResponse])
async def create_job(
    body: CreateScheduledJobRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard=Depends(require_non_auditor),
    service: ScheduledJobService = Depends(get_scheduled_job_service),
):
    channels = [NotifyChannel.model_validate(c.model_dump()) for c in body.notify_channels]
    job, secret = await service.create_job(
        owner_user_id=ctx.principal.user_id,
        name=body.name,
        trigger_type=body.trigger_type,
        trigger_spec=body.trigger_spec,
        prompt_template=body.prompt_template,
        skill_id=body.skill_id,
        model_id=body.model_id,
        codebase_id=body.codebase_id,
        knowledge_base_id=body.knowledge_base_id,
        notify_channels=channels,
        operator_scope=body.operator_scope,
        operator_domains=body.operator_domains,
        enabled=body.enabled,
        timezone=body.timezone,
        scope=ctx.scope,
    )
    return ApiResponse.success(
        CreateScheduledJobResponse(job=_job_response(job), webhook_secret=secret)
    )


@scheduled_router.patch("/{job_id}", response_model=ApiResponse[ScheduledJobResponse])
async def update_job(
    job_id: str,
    body: UpdateScheduledJobRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ScheduledJobService = Depends(get_scheduled_job_service),
):
    channels = None
    if body.notify_channels is not None:
        channels = [NotifyChannel.model_validate(c.model_dump()) for c in body.notify_channels]
    job = await service.patch_job(
        job_id,
        ctx.scope,
        name=body.name,
        trigger_type=body.trigger_type,
        trigger_spec=body.trigger_spec,
        prompt_template=body.prompt_template,
        skill_id=body.skill_id,
        model_id=body.model_id,
        codebase_id=body.codebase_id,
        knowledge_base_id=body.knowledge_base_id,
        notify_channels=channels,
        operator_scope=body.operator_scope,
        operator_domains=body.operator_domains,
        enabled=body.enabled,
        timezone=body.timezone,
    )
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ApiResponse.success(_job_response(job))


@scheduled_router.delete("/{job_id}", response_model=ApiResponse[dict])
async def delete_job(
    job_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ScheduledJobService = Depends(get_scheduled_job_service),
):
    job = await service.get_job(job_id, scope=ctx.scope)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    await service.delete_job(job_id, scope=ctx.scope)
    return ApiResponse.success({"deleted": True})


@scheduled_router.post("/{job_id}/rotate-secret", response_model=ApiResponse[WebhookSecretResponse])
async def rotate_secret(
    job_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ScheduledJobService = Depends(get_scheduled_job_service),
):
    job = await service.get_job(job_id, scope=ctx.scope)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    secret, token = await service.rotate_webhook_secret(job_id, scope=ctx.scope)
    if not secret or not token:
        raise HTTPException(status_code=400, detail="无法轮换密钥")
    return ApiResponse.success(WebhookSecretResponse(webhook_secret=secret, webhook_token=token))


@scheduled_router.post("/{job_id}/trigger", response_model=ApiResponse[dict])
async def trigger_job_now(
    job_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard=Depends(require_non_auditor),
    service: ScheduledJobService = Depends(get_scheduled_job_service),
):
    job = await service.get_job(job_id, scope=ctx.scope)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        session_id = await service.manual_trigger(
            job_id,
            ctx.principal.user_id,
            scope=ctx.scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not session_id:
        raise HTTPException(status_code=400, detail="任务触发失败")
    return ApiResponse.success({"session_id": session_id})


@notification_router.get("", response_model=ApiResponse[NotificationListResponse])
async def list_notifications(
    unread_only: bool = False,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: NotificationService = Depends(get_notification_service),
):
    items = await service.list_for_user(ctx.principal.user_id, unread_only=unread_only)
    unread = await service.count_unread(ctx.principal.user_id)
    return ApiResponse.success(
        NotificationListResponse(
            notifications=[NotificationResponse.model_validate(n.model_dump()) for n in items],
            unread_count=unread,
        )
    )


@notification_router.post("/{notification_id}/read", response_model=ApiResponse[dict])
async def mark_notification_read(
    notification_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: NotificationService = Depends(get_notification_service),
):
    await service.mark_read(notification_id, ctx.principal.user_id)
    return ApiResponse.success({"read": True})


@notification_router.get("/stream")
async def notification_stream(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: NotificationService = Depends(get_notification_service),
    streams: NotificationStreamFactory = Depends(get_notification_stream_factory),
):
    user_id = ctx.principal.user_id

    async def event_generator():
        unread = await finish_snapshot_before_cancellation(
            service.list_for_user(user_id, unread_only=True)
        )
        yield ServerSentEvent(
            event="connected",
            data=json.dumps({"user_id": user_id, "unread_count": len(unread)}),
        )
        try:
            async with streams.open(user_id) as stream:
                while True:
                    poll = await stream.poll(timeout_seconds=30.0)
                    if poll.payload is not None:
                        yield ServerSentEvent(event="notification", data=poll.payload)
                        continue
                    if not poll.connectivity.available:
                        await asyncio.sleep(1)
                    yield ServerSentEvent(event="ping", data="{}")
        except (OSError, RuntimeError, ValueError):
            while True:
                await asyncio.sleep(30)
                yield ServerSentEvent(event="ping", data="{}")

    return EventSourceResponse(event_generator())


@webhook_router.post("/webhooks/{job_token}")
async def webhook_trigger(
    job_token: str,
    request: Request,
    x_webhook_signature: str | None = Header(None, alias="X-Webhook-Signature"),
    service: ScheduledJobService = Depends(get_scheduled_job_service),
):
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {"raw": body.decode("utf-8", errors="replace")}
    with authorization_scope(AuthorizationContext.system("signed-webhook")):
        session_id, error = await service.trigger_webhook(
            job_token,
            body,
            x_webhook_signature or "",
            payload,
        )
    if error == "unauthorized":
        raise HTTPException(status_code=401, detail="Webhook 签名无效")
    if error == "not_found" or not session_id:
        raise HTTPException(status_code=404, detail="Webhook 无效")
    return {"session_id": session_id, "duplicate": error == "duplicate"}
