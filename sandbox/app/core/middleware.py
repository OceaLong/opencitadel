import hmac
import logging
import os

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.interfaces.service_dependencies import get_supervisor_service

logger = logging.getLogger(__name__)


async def auto_extend_timeout_middleware(request: Request, call_next):
    """使用中间件延长每次API请求是超时销毁时间"""
    # 1.获取系统配置与supervisor服务
    settings = get_settings()
    supervisor_service = get_supervisor_service()

    # 2.判断逻辑，仅在符合条件时延长超时销毁时间3分钟
    ignore_paths = (
        "/api/supervisor/activate-timeout",
        "/api/supervisor/extend-timeout",
        "/api/supervisor/cancel-timeout",
        "/api/supervisor/timeout-status",
    )
    if (
        settings.server_timeout_minutes is not None
        and supervisor_service.timeout_active
        and request.url.path.startswith("/api/")
        and not request.url.path.startswith(ignore_paths)
        and supervisor_service.expand_enabled
    ):
        try:
            await supervisor_service.extend_timeout(3)
            logger.debug("调用API请求而自动延长超时销毁时长: %s", request.url.path)
        except (OSError, RuntimeError, ValueError) as e:
            logger.warning("自动延长超时失败: %s", str(e))

    return await call_next(request)


async def require_sandbox_token_middleware(request: Request, call_next):
    """数据面 API 的 Bearer Token 强制校验（fail-closed）。

    kernel 为每个沙箱注入唯一的 SANDBOX_ACCESS_TOKEN（由 HMAC(seed, sandbox_id)
    派生），并在每次请求携带。校验规则：

    * token 未配置（env 为空）=> 直接拒绝(503)，绝不放行——消除此前"空 token 即
      完全不校验"的 fail-open 漏洞。正常启动路径下 config 已保证该 env 必填。
    * Authorization 头以恒定时间(hmac.compare_digest)比对，避免时序侧信道。

    这挡住了同一沙箱网络内其它容器对本沙箱数据面（shell/文件/CDP/VNC 反代）的
    越权访问。
    """
    if not request.url.path.startswith("/api/"):
        return await call_next(request)

    expected = os.environ.get("SANDBOX_ACCESS_TOKEN", "")
    if not expected:
        logger.error("SANDBOX_ACCESS_TOKEN 未配置，拒绝所有数据面请求")
        return JSONResponse(
            {"detail": "sandbox access token not configured"},
            status_code=503,
        )

    provided = request.headers.get("Authorization", "")
    if not hmac.compare_digest(provided, f"Bearer {expected}"):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)

    return await call_next(request)
