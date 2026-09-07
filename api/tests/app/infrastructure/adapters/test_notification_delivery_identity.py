from unittest.mock import MagicMock

import pytest

from app.infrastructure.adapters.outbound_notifier import HttpEmailOutboundNotifier


@pytest.mark.asyncio
async def test_smtp_retries_keep_stable_message_identity(monkeypatch):
    smtp = MagicMock()
    monkeypatch.setattr("app.infrastructure.adapters.outbound_notifier.smtplib.SMTP", smtp)
    adapter = HttpEmailOutboundNotifier(smtp_host="mail.example.com", smtp_use_tls=False)
    for _ in range(2):
        await adapter.send_email("a@example.com", "Notice", "body", delivery_id="delivery-1")
    messages = [
        call.args[0]
        for call in smtp.return_value.__enter__.return_value.send_message.call_args_list
    ]
    assert (
        messages[0]["Message-ID"]
        == messages[1]["Message-ID"]
        == "<delivery-1@notifications.opencitadel>"
    )
