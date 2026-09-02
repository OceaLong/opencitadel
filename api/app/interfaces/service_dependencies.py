"""Small FastAPI accessors for the four bounded-context runtimes."""

from __future__ import annotations

from fastapi import Depends, Request, WebSocket

from app.composition.types import ApiRuntime
from app.contexts.identity.runtime import IdentityRuntime
from app.contexts.inference.runtime import InferenceRuntime
from app.contexts.kernel.runtime import KernelApiRuntime
from app.contexts.knowledge.runtime import KnowledgeRuntime


class ApiRuntimeUnavailableError(RuntimeError):
    pass


def require_api_runtime(request: Request) -> ApiRuntime:
    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, ApiRuntime):
        raise ApiRuntimeUnavailableError("API runtime is not initialized")
    return runtime


def require_websocket_api_runtime(websocket: WebSocket) -> ApiRuntime:
    runtime = getattr(websocket.app.state, "runtime", None)
    if not isinstance(runtime, ApiRuntime):
        raise ApiRuntimeUnavailableError("API runtime is not initialized")
    return runtime


def get_identity_runtime(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> IdentityRuntime:
    return runtime.identity


def get_inference_runtime(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> InferenceRuntime:
    return runtime.inference


def get_knowledge_runtime(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> KnowledgeRuntime:
    return runtime.knowledge


def get_kernel_api_runtime(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> KernelApiRuntime:
    return runtime.kernel


__all__ = [
    "ApiRuntimeUnavailableError",
    "get_identity_runtime",
    "get_inference_runtime",
    "get_kernel_api_runtime",
    "get_knowledge_runtime",
    "require_api_runtime",
    "require_websocket_api_runtime",
]
