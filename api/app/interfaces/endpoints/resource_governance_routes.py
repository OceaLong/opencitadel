#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Owner-scoped resource version bindings and durable build-event replay."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, Query
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.domain.errors import BadRequestError
from app.application.services.resource_binding_service import ResourceBindingService
from app.application.services.resource_build_service import (
    MAX_EVENT_PAGE_SIZE,
    ResourceBuildService,
)
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceBuildEvent,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope, WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context, require_non_auditor
from app.interfaces.schemas import Response
from app.interfaces.schemas.session import (
    ResourceBindingResponse,
    UpgradeResourceBindingRequest,
    UpgradeResourceBindingResponse,
)
from app.interfaces.schemas.resource_governance import (
    ResourceBuildEventResponse,
)
from app.interfaces.service_dependencies import (
    get_resource_binding_service,
    get_resource_build_event_notifier,
    get_resource_build_service,
)

router = APIRouter(prefix="/sessions", tags=["资源版本绑定"])
build_router = APIRouter(prefix="/resource-builds", tags=["资源构建"])
_REPLAY_PAGE_SIZE = min(100, MAX_EVENT_PAGE_SIZE)
_HEARTBEAT_SECONDS = 15.0
_TERMINAL_BUILD_STATES = {
    BuildState.SUCCEEDED,
    BuildState.DEGRADED,
    BuildState.FAILED,
    BuildState.CANCELLED,
}


def _response(binding) -> ResourceBindingResponse:
    return ResourceBindingResponse(
        binding_id=binding.id,
        resource_kind=binding.resource_kind.value,
        resource_id=binding.resource_id,
        version_id=binding.version_id,
        is_current=binding.is_current,
        supersedes_binding_id=binding.supersedes_binding_id,
    )


@router.get("/{session_id}/resource-bindings", response_model=Response[list[ResourceBindingResponse]])
async def list_resource_bindings(
    session_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ResourceBindingService = Depends(get_resource_binding_service),
) -> Response[list[ResourceBindingResponse]]:
    return Response.success(data=[
        _response(binding)
        for binding in await service.current_bindings(session_id, ctx.scope)
    ])


@router.post(
    "/{session_id}/resource-bindings/{resource_kind}/upgrade",
    response_model=Response[UpgradeResourceBindingResponse],
)
async def upgrade_resource_binding(
    session_id: str,
    resource_kind: str,
    request: UpgradeResourceBindingRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard=Depends(require_non_auditor),
    service: ResourceBindingService = Depends(get_resource_binding_service),
) -> Response[UpgradeResourceBindingResponse]:
    try:
        kind = ResourceKind(resource_kind)
    except ValueError as exc:
        raise BadRequestError("invalid resource kind") from exc
    old = await service.current(session_id, kind, ctx.scope)
    new = await service.upgrade(
        session_id,
        kind,
        request.target_version_id,
        actor_id=ctx.principal.user_id,
        scope=ctx.scope,
    )
    return Response.success(data=UpgradeResourceBindingResponse(
        old_binding_id=old.id,
        new_binding_id=new.id,
        current_version_id=new.version_id,
    ))


@router.get(
    "/{session_id}/resource-bindings/{resource_kind}/available-versions",
    response_model=Response[list[ResourceBindingResponse]],
)
async def list_available_resource_versions(
    session_id: str,
    resource_kind: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ResourceBindingService = Depends(get_resource_binding_service),
) -> Response[list[ResourceBindingResponse]]:
    try:
        kind = ResourceKind(resource_kind)
    except ValueError as exc:
        raise BadRequestError("invalid resource kind") from exc
    return Response.success(data=[ResourceBindingResponse(
        binding_id="", resource_kind=version.resource_kind.value,
        resource_id=version.resource_id, version_id=version.version_id,
        is_current=False,
    ) for version in await service.available_versions(session_id, kind, ctx.scope)])


async def resource_build_event_generator(
    *,
    build_id: str,
    after: int,
    scope: OwnerScope,
    service: ResourceBuildService,
    notifier: Any,
    heartbeat_seconds: float = _HEARTBEAT_SECONDS,
    initial_build: ResourceBuild | None = None,
) -> AsyncGenerator[ServerSentEvent, None]:
    """Replay PostgreSQL first, then use Redis only as a refetch hint."""
    cursor = after
    build = initial_build or await service.validate_cursor(
        build_id,
        after,
        scope,
    )

    async def fetch_page() -> list[ResourceBuildEvent]:
        return await service.list_events(
            build_id,
            after_seq=cursor,
            limit=_REPLAY_PAGE_SIZE,
            scope=scope,
        )

    def response_event(
        event: ResourceBuildEvent,
        authoritative_build: ResourceBuild,
    ) -> ServerSentEvent:
        projection = ResourceBuildEventResponse.from_authoritative(
            authoritative_build,
            event,
        )
        return ServerSentEvent(
            id=str(event.seq),
            event="resource-build-event",
            data=projection.model_dump_json(),
        )

    # Fully replay committed rows before opening the lossy notification path.
    # An empty page is followed by an authoritative build re-read so a
    # transition committed between the query and state check cannot be missed.
    while True:
        page = await fetch_page()
        if page:
            build = await service.require_build(build_id, scope)
            for event in page:
                if event.seq <= cursor:
                    continue
                cursor = event.seq
                yield response_event(event, build)
                if event.state in _TERMINAL_BUILD_STATES:
                    return
            if len(page) == _REPLAY_PAGE_SIZE:
                continue
        build = await service.require_build(build_id, scope)
        if cursor < build.last_event_seq:
            # A row committed after the empty/short page; replay it before
            # considering Redis.
            continue
        if (
            build.state in _TERMINAL_BUILD_STATES
            and cursor == build.last_event_seq
        ):
            return
        if not page or len(page) < _REPLAY_PAGE_SIZE:
            break

    async with notifier.subscribe(build_id) as subscription:
        # Close the replay/subscribe race by immediately checking PostgreSQL,
        # then perform the same authoritative state re-read after every empty
        # catch-up.
        notification: dict[str, object] | None | object = object()
        while True:
            emitted = False
            while True:
                page = await fetch_page()
                if page:
                    build = await service.require_build(build_id, scope)
                    for event in page:
                        if event.seq <= cursor:
                            continue
                        cursor = event.seq
                        emitted = True
                        yield response_event(event, build)
                        if event.state in _TERMINAL_BUILD_STATES:
                            return
                    if len(page) == _REPLAY_PAGE_SIZE:
                        continue
                build = await service.require_build(build_id, scope)
                if cursor < build.last_event_seq:
                    continue
                if (
                    build.state in _TERMINAL_BUILD_STATES
                    and cursor == build.last_event_seq
                ):
                    return
                break

            if notification is None and not emitted:
                yield ServerSentEvent(event="ping", data="{}")

            notification = await subscription.wait(heartbeat_seconds)
            if notification is not None:
                notified_build = notification.get("build_id")
                notified_seq = notification.get("seq")
                if (
                    notified_build != build_id
                    or not isinstance(notified_seq, int)
                    or isinstance(notified_seq, bool)
                    or notified_seq < 1
                ):
                    notification = None


@build_router.get("/{build_id}/events")
async def stream_resource_build_events(
    build_id: str,
    after: int = Query(default=0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ResourceBuildService = Depends(get_resource_build_service),
    notifier=Depends(get_resource_build_event_notifier),
) -> EventSourceResponse:
    # Resolve access and cursor bounds before response headers are sent,
    # preserving owner-scoped 404 and stable ahead-cursor 400 semantics.
    initial_build = await service.validate_cursor(
        build_id,
        after,
        ctx.scope,
    )
    return EventSourceResponse(
        resource_build_event_generator(
            build_id=build_id,
            after=after,
            scope=ctx.scope,
            service=service,
            notifier=notifier,
            initial_build=initial_build,
        ),
        headers={"Cache-Control": "no-cache, no-transform"},
        ping=None,
    )
