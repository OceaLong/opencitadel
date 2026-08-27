import logging

from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from app.application.services.status_service import StatusService
from app.composition.types import ApiRuntime
from app.domain.models.health_status import HealthStatus
from app.interfaces.schemas import Response
from app.interfaces.service_dependencies import get_status_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/status", tags=["状态模块"])
health_router = APIRouter(prefix="/health", tags=["状态模块"])


@health_router.get("/live", summary="进程存活探针")
async def get_liveness() -> dict[str, str]:
    """Serving the event loop is sufficient for process liveness."""

    return {"status": "live"}


@health_router.get("/ready", summary="运行时就绪探针")
async def get_readiness(request: Request) -> JSONResponse:
    """Report whether the complete lifespan-owned runtime accepts traffic."""

    runtime = getattr(request.app.state, "runtime", None)
    ready = isinstance(runtime, ApiRuntime) and runtime.readiness.ready
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready"},
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    path="",
    response_model=Response[list[HealthStatus]],
    summary="系统健康检查",
    description="检查系统的postgres、redis、fastapi等组件的状态信息。",
)
async def get_status(
    status_service: StatusService = Depends(get_status_service),
) -> Response:
    """系统健康检查，检查postgres/redis/fastapi等服务"""
    statues = await status_service.check_all()

    if any(item.status == "error" for item in statues):
        return Response.fail(503, "系统存在服务异常", statues)

    return Response.success(data=statues)
