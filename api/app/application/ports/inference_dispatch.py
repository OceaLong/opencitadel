"""Private dispatch guard shared by resilience and every concrete transport send.

ContextVars keep concurrent invocations isolated without putting accounting data
in provider kwargs. E05 can implement this protocol or compose the durable guard.
"""

from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Protocol


class InferenceDispatchGuard(Protocol):
    async def before_send(self, model, payload: dict) -> str: ...
    async def after_send(self, identity: str, usage: dict, revision: str | None) -> None: ...


current_dispatch = ContextVar("inference_dispatch", default=(None, None))


@contextmanager
def dispatch_context(guard, model):
    token = current_dispatch.set((guard, model))
    try:
        yield
    finally:
        current_dispatch.reset(token)


@contextmanager
def dispatch_candidate(model):
    guard, _ = current_dispatch.get()
    with dispatch_context(guard, model):
        yield


class InferenceCandidateAuthority(Protocol):
    async def resolve(
        self, service, scope, primary, *, require_vision: bool, thinking_enabled: bool
    ) -> list: ...


current_candidate_authority = ContextVar("inference_candidate_authority", default=None)


@contextmanager
def candidate_authority_context(authority: InferenceCandidateAuthority):
    """Internal admission boundary; never populated from HTTP model hints."""
    token = current_candidate_authority.set(authority)
    try:
        yield
    finally:
        current_candidate_authority.reset(token)


@contextmanager
def physical_request_context(service, scope, purpose, model):
    """Keep accepted activity authority; otherwise bind this direct user request."""
    from app.application.security.authorization_context import get_authorization_context

    guard, _ = current_dispatch.get()
    if guard is None and service is not None:
        guard = service.guard(scope, get_authorization_context(), purpose=purpose)
    with dispatch_context(guard, model):
        yield


async def close_inference_adapter(adapter):
    from inspect import isawaitable

    close = getattr(adapter, "aclose", None)
    if close is not None:
        result = close()
        if isawaitable(result):
            await result


@contextmanager
def auxiliary_activity_context(service, request, context):
    if service is None:
        yield
        return
    guard = service.guard(
        scope=context.run.owner_scope,
        request=request,
        context=context,
        purpose="production",
        resolved={
            "policy_revision": str(context.run.policy_snapshot.execution_revision_id),
            "tool_fingerprint": None,
        },
    )
    if context.run.source_entity_type in {
        "evaluation_recorded_case",
        "evaluation_isolated_case",
        "evaluation_judge",
    }:
        guard = _UnsupportedEvaluationAuxiliary()
    with dispatch_context(guard, None):
        yield


class _UnsupportedEvaluationAuxiliary:
    async def before_send(self, model, payload):
        raise ValueError("budget_auxiliary_evaluation_profile_unavailable")


async def close_inference_adapters(adapters):
    """Dispose every distinct owned client even when another client fails to close."""
    import asyncio

    unique = {id(adapter): adapter for adapter in adapters if adapter is not None}
    results = await asyncio.gather(
        *(close_inference_adapter(adapter) for adapter in unique.values()),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result


@asynccontextmanager
async def owned_inference_adapter(adapter):
    """Close a newly allocated adapter without replacing its primary failure."""
    try:
        yield adapter
    except BaseException:
        try:
            await close_inference_adapter(adapter)
        except BaseException:  # noqa: BLE001 - preserve the primary cancellation/error
            import logging

            logging.getLogger(__name__).warning("inference adapter close failed during unwind")
        raise
    else:
        await close_inference_adapter(adapter)
