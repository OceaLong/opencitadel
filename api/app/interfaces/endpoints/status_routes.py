"""Liveness, readiness, and dependency status for the focused runtime."""

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from app.composition.types import ApiRuntime

router = APIRouter(prefix="/status", tags=["status"])
health_router = APIRouter(prefix="/health", tags=["status"])


@health_router.get("/live")
async def get_liveness() -> dict[str, str]:
    return {"status": "live"}


@health_router.get("/ready")
async def get_readiness(request: Request) -> JSONResponse:
    runtime = getattr(request.app.state, "runtime", None)
    ready = isinstance(runtime, ApiRuntime) and runtime.readiness.ready
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready"},
        headers={"Cache-Control": "no-store"},
    )


@router.get("")
async def get_status(request: Request) -> JSONResponse:
    runtime = getattr(request.app.state, "runtime", None)
    ready = isinstance(runtime, ApiRuntime) and runtime.readiness.ready
    redis_ready = ready and runtime.resources.redis_connectivity.available
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "data": {
                "status": "ok" if ready else "degraded",
                "postgres": "ready" if ready else "unavailable",
                "redis": "ready" if redis_ready else "optional-unavailable",
            }
        },
    )
