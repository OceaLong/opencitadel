import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.application.ports.streams import NotificationStreamFactory
from app.application.security.authorization_context import authorization_scope
from app.application.services.notification_service import NotificationService
from app.application.services.scheduled_job_service import ScheduledJobService
from app.domain.errors import BadRequestError, NotFoundError, UnauthorizedError
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scheduled_job import NotifyChannel
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context, require_non_auditor
from app.interfaces.schemas import Response as ApiResponse
from app.interfaces.schemas.notification import (
    NotificationDeliveryListResponse,
    NotificationDeliveryResponse,
    NotificationListResponse,
    NotificationResponse,
)
from app.interfaces.schemas.scheduled_job import (
    CreateScheduledJobRequest,
    CreateScheduledJobResponse,
    RunHistoryItem,
    RunHistoryListResponse,
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
        knowledge_base_id=body.knowledge_base_id,
        notify_channels=channels,
        operator_scope=body.operator_scope,
        operator_domains=body.operator_domains,
        enabled=body.enabled,
        timezone=body.timezone,
    )
    if not job:
        raise NotFoundError("任务不存在", error_key="apiErrors.scheduling.jobNotFound")
    return ApiResponse.success(_job_response(job))


@scheduled_router.delete("/{job_id}", response_model=ApiResponse[dict])
async def delete_job(
    job_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ScheduledJobService = Depends(get_scheduled_job_service),
):
    job = await service.get_job(job_id, scope=ctx.scope)
    if not job:
        raise NotFoundError("任务不存在", error_key="apiErrors.scheduling.jobNotFound")
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
        raise NotFoundError("任务不存在", error_key="apiErrors.scheduling.jobNotFound")
    secret, token = await service.rotate_webhook_secret(job_id, scope=ctx.scope)
    if not secret or not token:
        raise BadRequestError("无法轮换密钥", error_key="apiErrors.scheduling.secretRotationFailed")
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
        raise NotFoundError("任务不存在", error_key="apiErrors.scheduling.jobNotFound")
    try:
        session_id = await service.manual_trigger(
            job_id,
            ctx.principal.user_id,
            scope=ctx.scope,
        )
    except ValueError as exc:
        raise BadRequestError(str(exc), error_key="apiErrors.scheduling.triggerInvalid") from exc
    if not session_id:
        raise BadRequestError("任务触发失败", error_key="apiErrors.scheduling.triggerFailed")
    return ApiResponse.success({"session_id": session_id})


@scheduled_router.get("/{job_id}/runs", response_model=ApiResponse[RunHistoryListResponse])
async def list_job_runs(
    job_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ScheduledJobService = Depends(get_scheduled_job_service),
):
    """Return the authoritative execution-run history for a scheduled job.

    Ownership is enforced by the scoped job lookup inside the service; a job
    outside the caller's workspace scope yields a 404.
    """
    runs = await service.list_runs(job_id, ctx.scope, limit=limit, offset=offset)
    if runs is None:
        raise NotFoundError("任务不存在", error_key="apiErrors.scheduling.jobNotFound")
    return ApiResponse.success(
        RunHistoryListResponse(
            runs=[
                RunHistoryItem(
                    run_id=entry.run_id,
                    family=entry.family,
                    status=entry.status.value,
                    started_at=entry.created_at,
                    finished_at=entry.terminal_at,
                    error=entry.failure_code,
                )
                for entry in runs
            ]
        )
    )


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


@notification_router.post("/channels/validate", response_model=ApiResponse[dict])
async def validate_notification_channel(
    body: NotifyChannel,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard=Depends(require_non_auditor),
    service: NotificationService = Depends(get_notification_service),
):
    try:
        async with asyncio.timeout(20):
            await service.validate_channel(ctx.scope, body)
    except (OSError, RuntimeError, ValueError) as exc:
        raise BadRequestError(
            "Notification configuration could not be verified; check the server, tool and arguments"
        ) from exc
    return ApiResponse.success({"valid": True, "sent": False})


class TestNotificationChannelRequest(BaseModel):
    channel: NotifyChannel
    request_id: UUID


@notification_router.post("/channels/test", response_model=ApiResponse[dict])
async def enqueue_test_notification_channel(
    body: TestNotificationChannelRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard=Depends(require_non_auditor),
    service: NotificationService = Depends(get_notification_service),
):
    delivery_id = await service.queue_test_channel(
        ctx.scope, body.channel, request_id=str(body.request_id)
    )
    return ApiResponse.success({"delivery_id": delivery_id, "status": "pending"})


@notification_router.get(
    "/deliveries", response_model=ApiResponse[NotificationDeliveryListResponse]
)
async def list_notification_deliveries(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: NotificationService = Depends(get_notification_service),
):
    deliveries = await service.list_deliveries(ctx.principal.user_id)
    return ApiResponse.success(
        NotificationDeliveryListResponse(
            deliveries=[
                NotificationDeliveryResponse.model_validate(
                    {**item.model_dump(), "channel_type": item.channel["type"]}
                )
                for item in deliveries
            ]
        )
    )


@notification_router.get(
    "/deliveries/{delivery_id}", response_model=ApiResponse[NotificationDeliveryResponse]
)
async def get_notification_delivery(
    delivery_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: NotificationService = Depends(get_notification_service),
):
    item = await service.get_delivery(delivery_id, ctx.principal.user_id)
    if item is None:
        raise NotFoundError("Notification delivery not found")
    return ApiResponse.success(
        NotificationDeliveryResponse.model_validate(
            {**item.model_dump(), "channel_type": item.channel["type"]}
        )
    )


@notification_router.post("/deliveries/{delivery_id}/retry", response_model=ApiResponse[dict])
async def retry_notification_delivery(
    delivery_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard=Depends(require_non_auditor),
    service: NotificationService = Depends(get_notification_service),
):
    return ApiResponse.success(
        {"retried": await service.retry_delivery(delivery_id, ctx.principal.user_id)}
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
        raise UnauthorizedError(
            "Webhook 签名无效", error_key="apiErrors.scheduling.webhookSignatureInvalid"
        )
    if error == "not_found" or not session_id:
        raise NotFoundError("Webhook 无效", error_key="apiErrors.scheduling.webhookNotFound")
    return {"session_id": session_id, "duplicate": error == "duplicate"}
