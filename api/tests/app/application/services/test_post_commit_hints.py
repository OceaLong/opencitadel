from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.services.notification_service import NotificationService
from app.application.services.session_service import SessionService


class _NotificationUnitOfWork:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.notification = SimpleNamespace(save=self._save)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def _save(self, _notification) -> None:
        self._events.append("save")

    async def commit(self) -> None:
        self._events.append("commit")


class _SessionUnitOfWork:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.session = SimpleNamespace(save=self._save)
        self.inference_model = SimpleNamespace(get_by_id=AsyncMock(return_value=None))
        self.skill = SimpleNamespace(get_by_id=AsyncMock(return_value=None))
        self.knowledge_base = SimpleNamespace(get_kb=AsyncMock(return_value=None))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def _save(self, _session) -> None:
        self._events.append("save")

    async def commit(self) -> None:
        self._events.append("commit")


class _NotificationPublisher:
    def __init__(self, events: list[str], *, error: Exception | None = None) -> None:
        self._events = events
        self._error = error

    async def publish(self, _user_id: str, _payload: str):
        self._events.append("publish")
        if self._error is not None:
            raise self._error


class _SessionPublisher:
    def __init__(self, events: list[str], *, error: Exception | None = None) -> None:
        self._events = events
        self._error = error

    async def publish_changed(self):
        self._events.append("publish")
        if self._error is not None:
            raise self._error


def _notification_service(events: list[str], publisher) -> NotificationService:
    return NotificationService(
        uow_factory=lambda: _NotificationUnitOfWork(events),
        mcp_servers=AsyncMock(),
        mcp_connection_pool=AsyncMock(),
        policy_reader=AsyncMock(),
        publisher=publisher,
    )


@pytest.mark.asyncio
async def test_notification_is_published_after_commit() -> None:
    events: list[str] = []
    service = _notification_service(events, _NotificationPublisher(events))

    await service.send("user-1", "job_complete", "done")

    assert events == ["save", "commit", "publish"]


@pytest.mark.asyncio
async def test_patrol_completion_is_a_first_class_notification_type() -> None:
    events: list[str] = []
    service = _notification_service(events, _NotificationPublisher(events))

    created = await service.send("user-1", "patrol_complete", "patrol done")

    assert created.type == "patrol_complete"
    assert events == ["save", "commit", "publish"]


@pytest.mark.asyncio
async def test_failed_notification_hint_does_not_reverse_committed_write() -> None:
    events: list[str] = []
    service = _notification_service(
        events,
        _NotificationPublisher(events, error=ConnectionError("redis unavailable")),
    )

    created = await service.send("user-1", "job_complete", "done")

    assert created.user_id == "user-1"
    assert events == ["save", "commit", "publish"]


@pytest.mark.asyncio
async def test_failed_session_hint_does_not_reverse_committed_write() -> None:
    events: list[str] = []
    service = SessionService(
        uow_factory=lambda: _SessionUnitOfWork(events),
        sandbox_factory=AsyncMock(),
        run_projection=AsyncMock(),
        session_list_publisher=_SessionPublisher(
            events,
            error=ConnectionError("redis unavailable"),
        ),
    )

    created = await service.create_session(title="new")

    assert created.id
    assert events == ["save", "commit", "publish"]
