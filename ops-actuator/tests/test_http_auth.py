"""Bearer-token enforcement for the streamable-http transport.

The Actuator drives destructive writes, so an unauthenticated HTTP endpoint is a
P0 exposure. These tests pin the middleware's accept/reject behaviour and the
fail-closed startup validation without standing up a real socket.
"""

import pytest
from opencitadel_ops_actuator.config import ActuatorSettings
from opencitadel_ops_actuator.http_auth import (
    MIN_TOKEN_LENGTH,
    BearerTokenMiddleware,
    require_http_token,
)
from opencitadel_ops_actuator.server import build_http_app

STRONG_TOKEN = "a" * MIN_TOKEN_LENGTH


class _RecordingApp:
    def __init__(self) -> None:
        self.called = False

    async def __call__(self, scope, receive, send) -> None:
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def _drive(middleware, headers):
    scope = {"type": "http", "headers": headers}
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


def test_require_http_token_rejects_empty():
    with pytest.raises(RuntimeError, match="OPS_ACTUATOR_TOKEN"):
        require_http_token("")


def test_require_http_token_rejects_short():
    with pytest.raises(RuntimeError, match=str(MIN_TOKEN_LENGTH)):
        require_http_token("short")


def test_require_http_token_accepts_strong():
    assert require_http_token(f"  {STRONG_TOKEN}  ") == STRONG_TOKEN


@pytest.mark.asyncio
async def test_middleware_rejects_missing_authorization():
    inner = _RecordingApp()
    sent = await _drive(BearerTokenMiddleware(inner, STRONG_TOKEN), headers=[])
    assert inner.called is False
    assert sent[0]["status"] == 401


@pytest.mark.asyncio
async def test_middleware_rejects_wrong_token():
    inner = _RecordingApp()
    headers = [(b"authorization", b"Bearer wrong-token")]
    sent = await _drive(BearerTokenMiddleware(inner, STRONG_TOKEN), headers=headers)
    assert inner.called is False
    assert sent[0]["status"] == 401


@pytest.mark.asyncio
async def test_middleware_accepts_correct_token():
    inner = _RecordingApp()
    headers = [(b"authorization", f"Bearer {STRONG_TOKEN}".encode())]
    sent = await _drive(BearerTokenMiddleware(inner, STRONG_TOKEN), headers=headers)
    assert inner.called is True
    assert sent[0]["status"] == 200


@pytest.mark.asyncio
async def test_middleware_passes_through_non_http_scope():
    inner = _RecordingApp()
    scope = {"type": "lifespan"}

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        return None

    await BearerTokenMiddleware(inner, STRONG_TOKEN)(scope, receive, send)
    assert inner.called is True


def test_build_http_app_refuses_without_token():
    settings = ActuatorSettings(token="", allowed_namespaces=["opencitadel"])
    with pytest.raises(RuntimeError, match="OPS_ACTUATOR_TOKEN"):
        build_http_app(settings)


def test_build_http_app_builds_with_token():
    settings = ActuatorSettings(token=STRONG_TOKEN, allowed_namespaces=["opencitadel"])
    app = build_http_app(settings)
    assert app is not None
