#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""HTTP/SSE cursor replay for authoritative resource-build events."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceBuildEvent,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.domain.models.user import GlobalRole
from app.interfaces.auth_dependencies import enforce_auditor_read_only
from app.interfaces.endpoints import resource_governance_routes
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import (
    get_resource_build_event_notifier,
    get_resource_build_service,
)


def _event(seq: int) -> ResourceBuildEvent:
    return ResourceBuildEvent(
        id=f"event-{seq}",
        build_id="build-1",
        seq=seq,
        phase=f"phase-{seq}",
        state=(
            BuildState.SUCCEEDED
            if seq == 3
            else BuildState.RUNNING
        ),
        progress=seq / 3,
        payload={"authoritative": seq},
    )


class _ReplayService:
    def __init__(self, *, owner: str = "owner") -> None:
        self.owner = owner
        self.events = [_event(1), _event(2), _event(3)]
        self.calls: list[tuple[int, int, str]] = []
        self.require_calls = 0

    async def require_build(self, build_id: str, scope: OwnerScope):
        if build_id != "build-1" or scope.user_id != self.owner:
            raise NotFoundError("resource build not found in owner scope")
        self.require_calls += 1
        last = max((event.seq for event in self.events), default=0)
        last_event = next(
            (event for event in reversed(self.events) if event.seq == last),
            None,
        )
        return ResourceBuild(
            id=build_id,
            resource_kind=ResourceKind.KNOWLEDGE_BASE,
            resource_id="kb-1",
            version_id="kb-1-v2",
            command_key="reindex:kb-1",
            state=last_event.state if last_event else BuildState.QUEUED,
            phase=last_event.phase if last_event else None,
            progress=last_event.progress if last_event else 0,
            degraded_reasons=["VECTOR_UNAVAILABLE"],
            last_event_seq=last,
            created_by="owner",
        )

    async def validate_cursor(
        self,
        build_id: str,
        after_seq: int,
        scope: OwnerScope,
    ):
        build = await self.require_build(build_id, scope)
        if after_seq > build.last_event_seq:
            raise BadRequestError(
                "resource build event cursor is ahead of durable build cursor"
            )
        return build

    async def list_events(
        self,
        build_id: str,
        after_seq: int,
        limit: int,
        scope: OwnerScope,
    ):
        await self.require_build(build_id, scope)
        self.calls.append((after_seq, limit, scope.user_id))
        return [
            event for event in self.events if event.seq > after_seq
        ][:limit]


class _TerminalTransitionRaceService(_ReplayService):
    """Commit a terminal event between an empty replay and state re-read."""

    async def require_build(self, build_id: str, scope: OwnerScope):
        if self.require_calls == 2:
            self.events.append(
                _event(2).model_copy(
                    update={
                        "state": BuildState.SUCCEEDED,
                        "progress": 1.0,
                    }
                )
            )
        return await super().require_build(build_id, scope)


class _Subscription:
    def __init__(self, messages, on_wait=None) -> None:
        self.messages = list(messages)
        self.on_wait = on_wait
        self.closed = False

    async def wait(self, timeout: float):
        del timeout
        if self.on_wait is not None:
            callback, self.on_wait = self.on_wait, None
            callback()
        if self.messages:
            return self.messages.pop(0)
        return None

    async def close(self):
        self.closed = True


class _Notifier:
    def __init__(
        self,
        messages=(),
        on_wait=None,
        *,
        fail_subscribe: bool = False,
    ) -> None:
        self.subscription = _Subscription(messages, on_wait=on_wait)
        self.fail_subscribe = fail_subscribe
        self.subscribe_calls = 0

    @asynccontextmanager
    async def subscribe(self, _build_id: str):
        self.subscribe_calls += 1
        if self.fail_subscribe:
            raise AssertionError("terminal/high-cursor request subscribed")
        try:
            yield self.subscription
        finally:
            await self.subscription.close()


def _app(service, notifier) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    async def context() -> WorkspaceContext:
        principal = Principal(
            user_id="owner",
            global_role=GlobalRole.USER,
        )
        return WorkspaceContext(
            principal=principal,
            scope=OwnerScope.personal(principal.user_id),
        )

    app.include_router(
        resource_governance_routes.build_router,
        prefix="/api",
        dependencies=[Depends(enforce_auditor_read_only)],
    )
    app.dependency_overrides[
        resource_governance_routes.get_workspace_context
    ] = context
    app.dependency_overrides[enforce_auditor_read_only] = (
        lambda: Principal(user_id="owner", global_role=GlobalRole.USER)
    )
    app.dependency_overrides[get_resource_build_service] = lambda: service
    app.dependency_overrides[get_resource_build_event_notifier] = (
        lambda: notifier
    )
    return app


@pytest.mark.asyncio
async def test_event_generator_replays_postgres_then_dedupes_gap_notification():
    service = _ReplayService()
    service.events = [_event(1)]

    def commit_gap():
        service.events.extend([_event(2), _event(3)])

    # seq=3 arrives before seq=2 and seq=2 is duplicated. PG must define order.
    notifier = _Notifier(
        [
            {"build_id": "build-1", "seq": 3},
            {"build_id": "build-1", "seq": 2},
        ],
        on_wait=commit_gap,
    )
    generator = resource_governance_routes.resource_build_event_generator(
        build_id="build-1",
        after=1,
        scope=OwnerScope.personal("owner"),
        service=service,
        notifier=notifier,
        heartbeat_seconds=0.01,
    )

    first = await anext(generator)
    second = await anext(generator)
    with pytest.raises(StopAsyncIteration):
        await anext(generator)

    assert json.loads(first.data)["seq"] == 2
    assert json.loads(second.data)["seq"] == 3
    assert service.calls[0][0] == 1
    assert notifier.subscription.closed is True


@pytest.mark.asyncio
async def test_reconnect_replays_postgres_when_notification_history_is_empty():
    service = _ReplayService()
    notifier = _Notifier()
    generator = resource_governance_routes.resource_build_event_generator(
        build_id="build-1",
        after=2,
        scope=OwnerScope.personal("owner"),
        service=service,
        notifier=notifier,
        heartbeat_seconds=0.01,
    )

    replayed = await anext(generator)
    with pytest.raises(StopAsyncIteration):
        await anext(generator)

    assert json.loads(replayed.data)["seq"] == 3
    assert json.loads(replayed.data)["payload"] == {"authoritative": 3}
    # A replayed terminal event closes before a Redis subscription is needed.
    assert notifier.subscription.closed is False


@pytest.mark.asyncio
async def test_terminal_exact_cursor_closes_without_notification_subscription():
    service = _ReplayService()
    notifier = _Notifier(fail_subscribe=True)
    generator = resource_governance_routes.resource_build_event_generator(
        build_id="build-1",
        after=3,
        scope=OwnerScope.personal("owner"),
        service=service,
        notifier=notifier,
        heartbeat_seconds=0,
    )

    with pytest.raises(StopAsyncIteration):
        await anext(generator)

    assert notifier.subscribe_calls == 0
    assert notifier.subscription.closed is False


@pytest.mark.asyncio
async def test_empty_replay_rereads_terminal_transition_before_subscribing():
    service = _TerminalTransitionRaceService()
    service.events = [
        _event(1).model_copy(update={"state": BuildState.RUNNING})
    ]
    notifier = _Notifier(fail_subscribe=True)
    generator = resource_governance_routes.resource_build_event_generator(
        build_id="build-1",
        after=1,
        scope=OwnerScope.personal("owner"),
        service=service,
        notifier=notifier,
        heartbeat_seconds=0,
    )

    terminal = json.loads((await anext(generator)).data)
    with pytest.raises(StopAsyncIteration):
        await anext(generator)

    assert terminal["seq"] == 2
    assert terminal["state"] == "succeeded"
    assert notifier.subscribe_calls == 0


@pytest.mark.asyncio
async def test_disconnect_closes_live_notification_subscription():
    service = _ReplayService()
    service.events = [_event(1).model_copy(
        update={"state": BuildState.RUNNING}
    )]
    notifier = _Notifier()
    generator = resource_governance_routes.resource_build_event_generator(
        build_id="build-1",
        after=0,
        scope=OwnerScope.personal("owner"),
        service=service,
        notifier=notifier,
        heartbeat_seconds=0,
    )

    assert json.loads((await anext(generator)).data)["seq"] == 1
    assert (await anext(generator)).event == "ping"
    await generator.aclose()

    assert notifier.subscription.closed is True


def test_owner_http_stream_replays_committed_events_and_closes_at_terminal():
    app = _app(_ReplayService(owner="owner"), _Notifier())

    with TestClient(app) as client:
        response = client.get(
            "/api/resource-builds/build-1/events?after=1",
            headers={"accept": "text/event-stream"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: resource-build-event" in response.text
    assert '"seq":2' in response.text
    assert '"seq":3' in response.text
    assert '"seq":1' not in response.text
    projected = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert projected[0] == {
        "event": "resource_build",
        "id": "event-2",
        "seq": 2,
        "build_id": "build-1",
        "resource_kind": "knowledge_base",
        "resource_id": "kb-1",
        "version_id": "kb-1-v2",
        "phase": "phase-2",
        "state": "running",
        "progress": 2 / 3,
        "degraded_reasons": ["VECTOR_UNAVAILABLE"],
        "payload": {"authoritative": 2},
        "created_at": projected[0]["created_at"],
    }


def test_terminal_exact_cursor_http_stream_is_empty_and_never_subscribes():
    notifier = _Notifier(fail_subscribe=True)
    app = _app(_ReplayService(owner="owner"), notifier)

    with TestClient(app) as client:
        response = client.get(
            "/api/resource-builds/build-1/events?after=3",
            headers={"accept": "text/event-stream"},
        )

    assert response.status_code == 200
    assert response.text == ""
    assert notifier.subscribe_calls == 0


def test_http_rejects_cursor_ahead_of_durable_build_before_stream_headers():
    notifier = _Notifier(fail_subscribe=True)
    app = _app(_ReplayService(owner="owner"), notifier)

    with TestClient(app) as client:
        response = client.get(
            "/api/resource-builds/build-1/events?after=4",
            headers={"accept": "text/event-stream"},
        )

    assert response.status_code == 400
    assert response.json()["msg"] == (
        "resource build event cursor is ahead of durable build cursor"
    )
    assert notifier.subscribe_calls == 0


@pytest.mark.asyncio
async def test_public_projection_uses_authoritative_build_identity_exactly():
    service = _ReplayService()
    generator = resource_governance_routes.resource_build_event_generator(
        build_id="build-1",
        after=1,
        scope=OwnerScope.personal("owner"),
        service=service,
        notifier=_Notifier(),
        heartbeat_seconds=0,
    )

    sse = await anext(generator)
    payload = json.loads(sse.data)
    await generator.aclose()

    assert sse.event == "resource-build-event"
    assert payload == {
        "event": "resource_build",
        "id": "event-2",
        "seq": 2,
        "build_id": "build-1",
        "resource_kind": "knowledge_base",
        "resource_id": "kb-1",
        "version_id": "kb-1-v2",
        "phase": "phase-2",
        "state": "running",
        "progress": 2 / 3,
        "degraded_reasons": ["VECTOR_UNAVAILABLE"],
        "payload": {"authoritative": 2},
        "created_at": payload["created_at"],
    }


def test_build_event_route_is_mounted_validates_cursor_and_hides_foreign_build():
    service = _ReplayService(owner="somebody-else")
    app = _app(service, _Notifier())
    with TestClient(app) as client:
        invalid = client.get(
            "/api/resource-builds/build-1/events?after=-1",
            headers={"accept": "text/event-stream"},
        )
        foreign = client.get(
            "/api/resource-builds/build-1/events?after=0",
            headers={"accept": "text/event-stream"},
        )
        missing = client.get(
            "/api/resource-builds/missing/events?after=0",
            headers={"accept": "text/event-stream"},
        )

    assert invalid.status_code == 422
    assert foreign.status_code == 404
    assert missing.status_code == 404


def test_build_event_sse_route_requires_authentication_in_production_router():
    dependant = next(
        route.dependant
        for route in resource_governance_routes.build_router.routes
        if getattr(route, "path", "")
        == "/resource-builds/{build_id}/events"
    )

    assert resource_governance_routes.get_workspace_context in {
        dependency.call for dependency in dependant.dependencies
    }
