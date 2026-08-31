"""dispatch_notify_channels routes each channel to the right delivery method."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.services.notification_service import NotificationService
from app.domain.models.scope import OwnerScope


def _service(outbound: object) -> NotificationService:
    return NotificationService(
        uow_factory=lambda: None,
        mcp_servers=SimpleNamespace(),
        mcp_connection_pool=SimpleNamespace(),
        policy_reader=SimpleNamespace(),
        publisher=SimpleNamespace(),
        outbound_notifier=outbound,
    )


@pytest.mark.asyncio
async def test_dispatch_routes_webhook_and_email() -> None:
    # Regression: outbound webhook and SMTP were unimplemented. dispatch must
    # route webhook channels to send_webhook and email channels to send_email.
    outbound = SimpleNamespace(send_webhook=AsyncMock(), send_email=AsyncMock())
    service = _service(outbound)

    await service.dispatch_notify_channels(
        "user-1",
        OwnerScope.personal("user-1"),
        [
            {"type": "webhook", "url": "https://example.com/hook", "secret": "s"},
            {"type": "email", "address": "ops@example.com"},
        ],
        "hello",
    )

    outbound.send_webhook.assert_awaited_once()
    assert outbound.send_webhook.await_args.args[0] == "https://example.com/hook"
    outbound.send_email.assert_awaited_once()
    assert outbound.send_email.await_args.args[0] == "ops@example.com"


@pytest.mark.asyncio
async def test_dispatch_isolates_webhook_failure_from_email() -> None:
    outbound = SimpleNamespace(
        send_webhook=AsyncMock(side_effect=RuntimeError("endpoint down")),
        send_email=AsyncMock(),
    )
    service = _service(outbound)

    # A failing webhook must never block delivery on the email channel.
    await service.dispatch_notify_channels(
        "user-1",
        OwnerScope.personal("user-1"),
        [
            {"type": "webhook", "url": "https://x/hook"},
            {"type": "email", "address": "ops@example.com"},
        ],
        "hi",
    )

    outbound.send_email.assert_awaited_once()
