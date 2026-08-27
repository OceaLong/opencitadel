import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.application.dto.session_io import FileReadResult, ShellReadResult
from app.composition.types import ApiRuntime
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.domain.models.user import GlobalRole, UserStatus
from app.interfaces.endpoints.session.sandbox_routes import (
    _run_vnc_forwarders,
    _workspace_context_from_websocket,
    get_session_files,
    read_file,
    read_shell_output,
)
from app.interfaces.schemas.session import FileReadRequest, ShellReadRequest


def _context() -> WorkspaceContext:
    return WorkspaceContext(
        principal=Principal(user_id="user-1"),
        scope=OwnerScope.personal("user-1"),
    )


@pytest.mark.asyncio
async def test_vnc_forwarders_await_cancelled_peer_before_returning() -> None:
    sandbox_started = asyncio.Event()
    sandbox_cancelled = asyncio.Event()

    async def receive_bytes() -> bytes:
        await sandbox_started.wait()
        raise WebSocketDisconnect()

    async def receive_from_sandbox() -> bytes:
        sandbox_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            sandbox_cancelled.set()

    websocket = SimpleNamespace(
        receive_bytes=receive_bytes,
        send_bytes=AsyncMock(),
    )
    sandbox_websocket = SimpleNamespace(
        send=AsyncMock(),
        recv=receive_from_sandbox,
    )

    await _run_vnc_forwarders(websocket, sandbox_websocket)

    assert sandbox_cancelled.is_set()


@pytest.mark.asyncio
async def test_websocket_authentication_uses_the_lifespan_runtime() -> None:
    user = SimpleNamespace(
        id="user-1",
        status=UserStatus.ACTIVE,
        token_version=3,
        global_role=GlobalRole.USER,
    )

    class TokenCodec:
        def decode(self, token: str, expected_type: str):
            assert (token, expected_type) == ("valid-token", "access")
            return {"sub": "user-1", "ver": 3}

    class Uow:
        def __init__(self):
            self.user = SimpleNamespace(get_by_id=AsyncMock(return_value=user))
            self.team = SimpleNamespace(
                list_for_user=AsyncMock(return_value=[]),
                get_member=AsyncMock(return_value=None),
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    runtime = object.__new__(ApiRuntime)
    object.__setattr__(runtime, "token_codec", TokenCodec())
    object.__setattr__(runtime, "uow_factory", Uow)

    async def receive():
        return {"type": "websocket.disconnect"}

    async def send(_message):
        return None

    websocket = WebSocket(
        {
            "type": "websocket",
            "path": "/api/sessions/session-1/vnc",
            "headers": [(b"cookie", b"access_token=valid-token")],
            "query_string": b"",
            "scheme": "ws",
            "client": ("127.0.0.1", 1234),
            "server": ("api", 8000),
            "subprotocols": [],
        },
        receive,
        send,
    )

    context = await _workspace_context_from_websocket(websocket, runtime=runtime)

    assert context is not None
    assert context.principal.user_id == "user-1"
    assert context.scope == OwnerScope.personal("user-1")


@pytest.mark.asyncio
async def test_session_sandbox_http_routes_preserve_owner_scope() -> None:
    service = SimpleNamespace(
        get_session_files=AsyncMock(return_value=[]),
        read_file=AsyncMock(
            return_value=FileReadResult(filepath="/home/ubuntu/a.txt", content="a")
        ),
        read_shell_output=AsyncMock(
            return_value=ShellReadResult(session_id="shell-1", output="done")
        ),
    )
    context = _context()

    await get_session_files("session-1", ctx=context, session_service=service)
    await read_file(
        "session-1",
        FileReadRequest(filepath="/home/ubuntu/a.txt"),
        ctx=context,
        session_service=service,
    )
    await read_shell_output(
        "session-1",
        ShellReadRequest(session_id="shell-1"),
        ctx=context,
        session_service=service,
    )

    service.get_session_files.assert_awaited_once_with(
        "session-1",
        scope=context.scope,
    )
    service.read_file.assert_awaited_once_with(
        "session-1",
        "/home/ubuntu/a.txt",
        scope=context.scope,
    )
    service.read_shell_output.assert_awaited_once_with(
        "session-1",
        "shell-1",
        scope=context.scope,
    )
