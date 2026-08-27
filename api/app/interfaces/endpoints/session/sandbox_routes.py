# 本模块路由经 session_routes.py 的 .routes.extend() 聚合，勿在别处单独 include_router。
import asyncio
import logging

import websockets
from fastapi import APIRouter, Depends
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets import ConnectionClosed

from app.application.ports.crypto import ACCESS_COOKIE, TokenCodecError
from app.application.services.session_service import SessionService
from app.composition.types import ApiRuntime
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.domain.models.user import UserStatus
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.schemas import Response
from app.interfaces.schemas.session import (
    FileReadRequest,
    FileReadResponse,
    GetSessionFilesResponse,
    ShellReadRequest,
    ShellReadResponse,
)
from app.interfaces.service_dependencies import (
    get_session_service,
    require_websocket_api_runtime,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["会话模块"])


async def _run_vnc_forwarders(websocket: WebSocket, sandbox_ws) -> None:
    async def forward_to_sandbox() -> None:
        try:
            while True:
                data = await websocket.receive_bytes()
                await sandbox_ws.send(data)
        except WebSocketDisconnect:
            logger.info("Web->VNC连接终止")
        except (OSError, RuntimeError, ValueError) as exc:
            logger.error("forward_to_sandbox出错: %s", exc)

    async def forward_from_sandbox() -> None:
        try:
            while True:
                data = await sandbox_ws.recv()
                await websocket.send_bytes(data)
        except ConnectionClosed:
            logger.info("VNC->Web连接关闭")
        except (OSError, RuntimeError, ValueError) as exc:
            logger.error("forward_from_sandbox出错: %s", exc)

    tasks = (
        asyncio.create_task(forward_to_sandbox()),
        asyncio.create_task(forward_from_sandbox()),
    )
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _workspace_context_from_websocket(
    websocket: WebSocket,
    *,
    runtime: ApiRuntime,
) -> WorkspaceContext | None:
    """WebSocket 不经过 AuthContextMiddleware，这里显式从 cookie 构造工作区上下文。"""
    token = websocket.cookies.get(ACCESS_COOKIE)
    if not token:
        return None
    try:
        claims = runtime.token_codec.decode(token, expected_type="access")
    except TokenCodecError:
        return None
    user_id = str(claims.get("sub") or "")
    if not user_id:
        return None
    async with runtime.uow_factory() as uow:
        user = await uow.user.get_by_id(user_id)
        if not user or user.status != UserStatus.ACTIVE:
            return None
        if int(claims.get("ver", -1)) != user.token_version:
            return None
        teams = await uow.team.list_for_user(user_id)
        team_roles = {}
        for team in teams:
            member = await uow.team.get_member(team.id, user_id)
            if member:
                team_roles[team.id] = member.role
    principal = Principal(
        user_id=user.id,
        global_role=user.global_role,
        token_version=user.token_version,
        team_roles=team_roles,
    )
    workspace_id = (websocket.headers.get("x-workspace-id") or "").strip()
    if workspace_id:
        if workspace_id not in team_roles:
            return None
        return WorkspaceContext(principal=principal, scope=OwnerScope.team(user.id, workspace_id))
    return WorkspaceContext(principal=principal, scope=OwnerScope.personal(user.id))


@router.get(
    path="/{session_id}/files",
    response_model=Response[GetSessionFilesResponse],
    summary="获取指定任务会话文件列表信息",
    description="获取指定任务会话文件列表信息",
)
async def get_session_files(
    session_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session_service: SessionService = Depends(get_session_service),
) -> Response[GetSessionFilesResponse]:
    """获取指定任务会话文件列表信息"""
    files = await session_service.get_session_files(session_id, scope=ctx.scope)
    return Response.success(data=GetSessionFilesResponse(files=files))


@router.post(
    path="/{session_id}/file",
    response_model=Response[FileReadResponse],
    summary="查看会话沙箱中指定文件的内容",
    description="根据传递的会话id+文件路径查看沙箱中文件的内容信息",
)
async def read_file(
    session_id: str,
    request: FileReadRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session_service: SessionService = Depends(get_session_service),
) -> Response[FileReadResponse]:
    """根据传递的会话id+文件路径查看沙箱中文件的内容信息"""
    result = await session_service.read_file(session_id, request.filepath, scope=ctx.scope)
    return Response.success(data=result)


@router.post(
    path="/{session_id}/shell",
    response_model=Response[ShellReadResponse],
    summary="查看会话的shell内容输出",
    description="传递指定会话id与shell会话标识，查看shell内容输出",
)
async def read_shell_output(
    session_id: str,
    request: ShellReadRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session_service: SessionService = Depends(get_session_service),
) -> Response[ShellReadResponse]:
    """查看会话的shell内容输出"""
    result = await session_service.read_shell_output(
        session_id, request.session_id, scope=ctx.scope
    )
    return Response.success(
        data=result,
    )


@router.websocket(
    path="/{session_id}/vnc",
)
async def vnc_websocket(
    websocket: WebSocket,
    session_id: str,
    session_service: SessionService = Depends(get_session_service),
    runtime: ApiRuntime = Depends(require_websocket_api_runtime),
) -> None:
    """VNC Websocket端点，用于建立与沙箱环境的vnc连接，并双向转发数据"""
    ctx = await _workspace_context_from_websocket(websocket, runtime=runtime)
    if ctx is None:
        await websocket.close(code=1008, reason="Unauthorized")
        return
    from app.application.security.authorization_context import (
        reset_authorization_context,
        set_authorization_context,
    )
    from app.domain.models.authorization import AuthorizationContext

    authorization_token = set_authorization_context(
        AuthorizationContext.for_principal(ctx.principal, scope=ctx.scope)
    )

    # 1.从客户端noVNC接收子协议
    protocols_str = websocket.headers.get("sec-websocket-protocol", "")
    protocols = [p.strip() for p in protocols_str.split(",")]

    # 2.判断使用不同协议(noVNC首选binary)
    selected_protocol = None
    if "binary" in protocols:
        selected_protocol = "binary"
    elif "base64" in protocols:
        selected_protocol = "base64"

    # 3.使用对应协议接收websocket连接
    logger.info("为会话[%s]开启WebSocket连接", session_id)
    await websocket.accept(subprotocol=selected_protocol)

    try:
        # 4.获取对应会话的vnc链接
        sandbox_vnc_url = await session_service.get_vnc_url(session_id, scope=ctx.scope)
        logger.info("连接WebSocket VNC： %s", sandbox_vnc_url)

        # 5.创建上下文并连接到vnc
        async with websockets.connect(sandbox_vnc_url) as sandbox_ws:
            await _run_vnc_forwarders(websocket, sandbox_ws)
            logger.info("WebSocket连接已关闭")
    except ConnectionError as connection_e:
        # 连接沙箱环境失败，关闭websocket
        logger.error("连接沙箱环境失败: %s", connection_e)
        await websocket.close(code=1011, reason=f"连接沙箱环境失败: {connection_e!s}")
    except (OSError, RuntimeError, ValueError) as e:
        # 其他错误记录日志并关闭websocket
        logger.error("WebSocket异常: %s", e)
        await websocket.close(code=1011, reason=f"WebSocket异常: {e!s}")
    finally:
        reset_authorization_context(authorization_token)
