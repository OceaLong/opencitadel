"""Static bearer-token authentication for the streamable-http transport.

Even though the Collector is read-only, an unauthenticated MCP endpoint lets any
peer on the network enumerate pods, logs, events and dependency health, so the
HTTP transport must be gated. Mirroring the sandbox broker
(api/app/infrastructure/external/sandbox/broker.py), every HTTP request must
carry ``Authorization: Bearer <token>`` and is compared with
``hmac.compare_digest``. A streamable-http process refuses to start unless a
strong token (>= MIN_TOKEN_LENGTH characters) is configured; stdio (local dev,
no network boundary) does not require one.
"""

from __future__ import annotations

import hmac

from starlette.types import ASGIApp, Receive, Scope, Send

MIN_TOKEN_LENGTH = 32
ENV_VAR_NAME = "OPS_COLLECTOR_TOKEN"

_UNAUTHORIZED_BODY = b'{"error":"unauthorized"}'


def require_http_token(token: str) -> str:
    """Return the validated token or raise if it is too weak for network use."""
    cleaned = token.strip()
    if len(cleaned) < MIN_TOKEN_LENGTH:
        raise RuntimeError(
            f"{ENV_VAR_NAME} must contain at least {MIN_TOKEN_LENGTH} characters "
            "for streamable-http transport"
        )
    return cleaned


class BearerTokenMiddleware:
    """Reject any HTTP request lacking the exact ``Bearer <token>`` header."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        provided = ""
        for key, value in scope.get("headers", []):
            if key == b"authorization":
                provided = value.decode("latin-1")
                break
        if not hmac.compare_digest(provided, self._expected):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})
            return
        await self._app(scope, receive, send)


__all__ = ["ENV_VAR_NAME", "MIN_TOKEN_LENGTH", "BearerTokenMiddleware", "require_http_token"]
