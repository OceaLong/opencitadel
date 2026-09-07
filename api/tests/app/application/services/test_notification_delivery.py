from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.application.services.notification_service import NotificationService
from app.domain.models.scheduled_job import NotifyChannel
from app.domain.models.scope import OwnerScope


def service(client):
    return NotificationService(
        lambda: None,
        SimpleNamespace(
            resolve_mcp_runtime=AsyncMock(
                return_value=SimpleNamespace(servers={"s": SimpleNamespace(enabled=True)})
            )
        ),
        SimpleNamespace(acquire=AsyncMock(return_value=client)),
        SimpleNamespace(
            active_execution=AsyncMock(
                return_value=SimpleNamespace(
                    revision=SimpleNamespace(policy=SimpleNamespace(activity=object()))
                )
            )
        ),
        SimpleNamespace(),
    )


@pytest.mark.parametrize(
    "channel",
    [
        {"type": "mcp", "server_id": "s"},
        {"type": "invalid"},
        {"type": "email", "address": "bad"},
        {"type": "webhook", "url": "file:///etc/passwd"},
    ],
)
def test_rejects_incomplete_or_unknown_channel(channel):
    with pytest.raises(ValidationError):
        NotifyChannel.model_validate(channel)


@pytest.mark.asyncio
async def test_exact_mcp_tool_and_schema_mapped_arguments():
    client = SimpleNamespace(
        get_all_tools=AsyncMock(
            return_value=[
                {"function": {"name": "get_messages", "parameters": {"type": "object"}}},
                {
                    "function": {
                        "name": "send_message",
                        "parameters": {
                            "type": "object",
                            "properties": {"room": {"type": "string"}, "body": {"type": "string"}},
                            "required": ["room", "body"],
                            "additionalProperties": False,
                        },
                    }
                },
            ]
        ),
        invoke=AsyncMock(return_value={"isError": False}),
    )
    await service(client).send_im_via_mcp(
        "u",
        OwnerScope.personal("u"),
        [
            {
                "type": "mcp",
                "server_id": "s",
                "tool_name": "send_message",
                "message_arg": "body",
                "arguments": {"room": "ops"},
            }
        ],
        "hello",
    )
    client.invoke.assert_awaited_once_with("send_message", {"room": "ops", "body": "hello"})


@pytest.mark.asyncio
async def test_mcp_schema_error_prevents_send():
    client = SimpleNamespace(
        get_all_tools=AsyncMock(
            return_value=[
                {
                    "function": {
                        "name": "send",
                        "parameters": {
                            "type": "object",
                            "required": ["room"],
                            "properties": {"room": {"type": "integer"}, "text": {"type": "string"}},
                        },
                    }
                }
            ]
        ),
        invoke=AsyncMock(),
    )
    with pytest.raises(ValueError, match="arguments do not match"):
        await service(client).send_im_via_mcp(
            "u",
            OwnerScope.personal("u"),
            [
                {
                    "server_id": "s",
                    "tool_name": "send",
                    "message_arg": "text",
                    "arguments": {"room": "wrong"},
                }
            ],
            "hello",
        )
    client.invoke.assert_not_awaited()


class MemoryDeliveries:
    def __init__(self):
        self.rows = {}

    async def enqueue_delivery(self, delivery):
        self.rows.setdefault(delivery.id, delivery)

    async def claim_deliveries(self, *, now, limit, lease_seconds):
        rows = [
            r
            for r in self.rows.values()
            if r.status in {"pending", "retrying"} and r.next_attempt_at <= now
        ][:limit]
        for row in rows:
            row.status = "sending"
            row.attempts += 1
        return rows

    async def finish_delivery(self, delivery):
        self.rows[delivery.id] = delivery


class MemoryUow:
    def __init__(self):
        self.notification = MemoryDeliveries()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_outbox_deduplicates_and_retries_only_failed_channel():
    uow = MemoryUow()
    outbound = SimpleNamespace(
        send_webhook=AsyncMock(side_effect=OSError("down")), send_email=AsyncMock()
    )
    svc = NotificationService(
        lambda: uow,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        outbound,
    )
    channels = [
        {"type": "webhook", "url": "https://example.com"},
        {"type": "email", "address": "a@example.com"},
    ]
    for _ in range(2):
        await svc.queue_channels(
            uow,
            "u",
            OwnerScope.personal("u"),
            channels,
            "finished",
            idempotency_key="run:1:completed",
        )
    assert len(uow.notification.rows) == 2
    outbound.send_email.assert_not_awaited()
    assert await svc.process_deliveries() == 2
    rows = list(uow.notification.rows.values())
    assert [r.status for r in rows] == ["retrying", "sent"]
    assert rows[0].last_error
    assert rows[0].attempts == 1
    assert await svc.process_deliveries() == 0
    outbound.send_email.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "notice_type"),
    [("completed", "job_complete"), ("failed", "job_failed"), ("cancelled", "job_cancelled")],
)
async def test_scheduled_terminal_queues_in_same_transaction(status, notice_type):
    from app.domain.models.scheduled_job import ScheduledJob
    from tests.app.application.services.test_scheduled_job_runs import _service

    job = ScheduledJob(
        id="j",
        name="job",
        owner_user_id="u",
        last_run_status="running",
        last_run_session_id="s",
        notify_channels=[NotifyChannel(type="email", address="a@example.com")],
    )
    uow = MemoryUow()
    uow.scheduled_job = SimpleNamespace(
        get_by_last_run_session_id=AsyncMock(return_value=job), save=AsyncMock()
    )
    saved = []
    uow.notification.save = AsyncMock(side_effect=lambda n: saved.append(n))
    svc = _service(None, SimpleNamespace())
    svc._uow_factory = lambda: uow
    svc._notification_service = NotificationService(
        lambda: uow, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
    )
    await svc.on_session_terminal("s", status)
    await svc.on_session_terminal("s", status)
    assert len(saved) == 1
    assert saved[0].type == notice_type
    assert len(uow.notification.rows) == 1


@pytest.mark.asyncio
async def test_trigger_failure_persists_notification_with_job():
    from app.domain.models.scheduled_job import ScheduledJob
    from tests.app.application.services.test_scheduled_job_runs import _service

    job = ScheduledJob(
        id="j",
        name="job",
        owner_user_id="u",
        notify_channels=[NotifyChannel(type="email", address="a@example.com")],
    )
    uow = MemoryUow()
    uow.scheduled_job = SimpleNamespace(save=AsyncMock())
    saved = []
    uow.notification.save = AsyncMock(side_effect=lambda n: saved.append(n))
    svc = _service(None, SimpleNamespace())
    svc._uow_factory = lambda: uow
    svc._notification_service = NotificationService(
        lambda: uow, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
    )
    await svc.record_trigger_failure(job, "admission unavailable")
    assert saved[0].type == "job_failed"
    assert len(uow.notification.rows) == 1


@pytest.mark.asyncio
async def test_mcp_declared_failure_is_recorded_as_failure():
    from app.domain.models.tool_result import ToolResult

    client = SimpleNamespace(
        get_all_tools=AsyncMock(
            return_value=[{"function": {"name": "send", "parameters": {"type": "object"}}}]
        ),
        invoke=AsyncMock(return_value=ToolResult(success=False, message="secret response")),
    )
    with pytest.raises(RuntimeError, match="reported delivery failure"):
        await service(client).send_im_via_mcp(
            "u",
            OwnerScope.personal("u"),
            [{"server_id": "s", "tool_name": "send", "message_arg": "text"}],
            "hello",
        )


@pytest.mark.asyncio
async def test_retry_exhaustion_redacts_adapter_errors():
    from app.domain.utils.time_utils import utc_now

    uow = MemoryUow()
    outbound = SimpleNamespace(send_email=AsyncMock(side_effect=RuntimeError("password=SECRET")))
    svc = NotificationService(
        lambda: uow,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        outbound,
    )
    await svc.queue_channels(
        uow,
        "u",
        OwnerScope.personal("u"),
        [{"type": "email", "address": "a@example.com"}],
        "m",
        idempotency_key="event",
    )
    row = next(iter(uow.notification.rows.values()))
    for _ in range(5):
        row.next_attempt_at = utc_now()
        await svc.process_deliveries()
    assert row.status == "failed"
    assert row.attempts == 5
    assert "SECRET" not in row.last_error
    assert await svc.process_deliveries() == 0


@pytest.mark.asyncio
async def test_configuration_validation_never_invokes_tool():
    client = SimpleNamespace(
        get_all_tools=AsyncMock(
            return_value=[
                {
                    "function": {
                        "name": "send",
                        "parameters": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                        },
                    }
                }
            ]
        ),
        invoke=AsyncMock(),
    )
    await service(client).validate_channel(
        OwnerScope.personal("u"), NotifyChannel(server_id="s", tool_name="send")
    )
    client.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_schema_external_reference_is_rejected_without_fetching():
    client = SimpleNamespace(
        get_all_tools=AsyncMock(
            return_value=[
                {
                    "function": {
                        "name": "send",
                        "parameters": {"type": "object", "$ref": "https://example.com/schema"},
                    }
                }
            ]
        ),
        invoke=AsyncMock(),
    )
    with pytest.raises(ValueError, match="External schema references"):
        await service(client).validate_channel(
            OwnerScope.personal("u"), NotifyChannel(server_id="s", tool_name="send")
        )
    client.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_test_notification_is_queued_idempotently_without_sending():
    uow = MemoryUow()
    outbound = SimpleNamespace(send_email=AsyncMock())
    svc = NotificationService(
        lambda: uow,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        outbound,
    )
    channel = NotifyChannel(type="email", address="a@example.com")
    first = await svc.queue_test_channel(OwnerScope.personal("u"), channel, request_id="request-1")
    second = await svc.queue_test_channel(OwnerScope.personal("u"), channel, request_id="request-1")
    assert first == second
    assert len(uow.notification.rows) == 1
    assert next(iter(uow.notification.rows.values())).status == "pending"
    outbound.send_email.assert_not_awaited()


def test_message_and_idempotency_parameters_cannot_overlap():
    with pytest.raises(ValidationError, match="distinct"):
        NotifyChannel(server_id="s", tool_name="send", message_arg="text", idempotency_arg="text")
