"""Public execution-event query types and integrity-protected cursors."""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue


class PublicEventCursor:
    def __init__(self, *, secret: bytes, previous_secrets: tuple[bytes, ...] = ()) -> None:
        """Integrity-protected cursor codec.

        ``previous_secrets`` supports runtime key rotation (K4-5): new cursors
        are always signed with ``secret``, while decoding also accepts cursors
        signed with any previous secret so in-flight clients survive the
        rotation window.
        """
        if len(secret) < 16:
            raise ValueError("cursor secret must be at least 16 bytes")
        for previous in previous_secrets:
            if len(previous) < 16:
                raise ValueError("cursor secret must be at least 16 bytes")
        self._secret = secret
        self._decode_secrets = (secret, *previous_secrets)

    def encode(self, position: int) -> str:
        if position < 1:
            raise ValueError("cursor position must be positive")
        payload = position.to_bytes(8, "big", signed=False)
        signature = hmac.new(self._secret, payload, hashlib.sha256).digest()[:16]
        return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")

    def decode(self, cursor: str) -> int:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
        except (ValueError, TypeError) as error:
            raise ValueError("invalid public event cursor") from error
        if len(decoded) != 24:
            raise ValueError("invalid public event cursor")
        payload, candidate = decoded[:8], decoded[8:]
        if not any(
            hmac.compare_digest(
                candidate,
                hmac.new(secret, payload, hashlib.sha256).digest()[:16],
            )
            for secret in self._decode_secrets
        ):
            raise ValueError("invalid public event cursor")
        position = int.from_bytes(payload, "big", signed=False)
        if position < 1:
            raise ValueError("invalid public event cursor")
        return position


class PublicExecutionEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cursor: str
    event_id: UUID
    event_type: str
    run_id: UUID | None
    stream_id: str
    stream_version: int
    payload: dict[str, JsonValue]
    occurred_at: datetime


class PublicEventPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    events: tuple[PublicExecutionEvent, ...]
    next_cursor: str | None
    prev_cursor: str | None
    has_earlier: bool


__all__ = [
    "PublicEventCursor",
    "PublicEventPage",
    "PublicExecutionEvent",
]
