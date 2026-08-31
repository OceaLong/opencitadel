"""Port for delivering notifications to external channels (webhook, email)."""

from typing import Any, Protocol


class OutboundNotifierPort(Protocol):
    async def send_webhook(self, url: str, secret: str, payload: dict[str, Any]) -> None:
        """POST the payload to the webhook url with an HMAC-SHA256 signature."""
        ...

    async def send_email(self, address: str, subject: str, body: str) -> None:
        """Send a plain-text email to the recipient address."""
        ...
