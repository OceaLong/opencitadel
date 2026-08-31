"""Adapter delivering notifications to external webhook and email channels."""

import asyncio
import hashlib
import hmac
import json
import smtplib
from email.message import EmailMessage
from typing import Any

from app.application.ports.crypto import OutboundNetworkPolicy
from app.domain.external.outbound_notifier import OutboundNotifierPort
from app.infrastructure.security.outbound_http import create_ssrf_safe_async_client


class HttpEmailOutboundNotifier(OutboundNotifierPort):
    def __init__(
        self,
        *,
        outbound_policy: OutboundNetworkPolicy | None = None,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        smtp_from: str = "",
        smtp_use_tls: bool = True,
    ) -> None:
        self._outbound_policy = outbound_policy
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_user = smtp_user
        self._smtp_password = smtp_password
        self._smtp_from = smtp_from
        self._smtp_use_tls = smtp_use_tls

    async def send_webhook(self, url: str, secret: str, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if secret:
            signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers["X-OpenCitadel-Signature"] = f"sha256={signature}"
        # The SSRF-safe client pins DNS and enforces the outbound port/host
        # policy, so a webhook url can't be used to reach internal services.
        async with create_ssrf_safe_async_client(
            timeout=15.0, outbound_policy=self._outbound_policy
        ) as client:
            response = await client.post(url, content=body, headers=headers)
            response.raise_for_status()

    async def send_email(self, address: str, subject: str, body: str) -> None:
        if not self._smtp_host:
            raise RuntimeError("SMTP 未配置：请设置 SMTP_HOST 等环境变量")

        def _send() -> None:
            message = EmailMessage()
            message["From"] = self._smtp_from or self._smtp_user
            message["To"] = address
            message["Subject"] = subject
            message.set_content(body)
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=15) as server:
                if self._smtp_use_tls:
                    server.starttls()
                if self._smtp_user:
                    server.login(self._smtp_user, self._smtp_password)
                server.send_message(message)

        await asyncio.to_thread(_send)
