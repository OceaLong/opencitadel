"""Application contracts for lossy hints and owned Redis streams."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.application.ports.coordination import RedisConnectivity
from app.application.ports.execution import WakeupMessage, WakeupPublisherPort

SESSION_LIST_HINT_CHANNEL = "sessions:list:notify"
NOTIFICATION_HINT_CHANNEL_PREFIX = "notify:"
RUNTIME_POLICY_HINT_CHANNEL = "runtime_policy:changed"


@dataclass(frozen=True)
class WakeupBatch:
    cursor: str
    messages: tuple[WakeupMessage, ...]
    connectivity: RedisConnectivity


@dataclass(frozen=True)
class HintPoll:
    payload: str | None
    connectivity: RedisConnectivity


@runtime_checkable
class WakeupPort(WakeupPublisherPort, Protocol):
    """Competing consumption: each hint reaches exactly one reader.

    This is the kernel replicas' mode (consumer group) — one replica wakes for
    a hint, the rest keep sleeping. SSE listeners must NOT use it: they would
    steal hints from the kernel and from each other.
    """

    async def read(
        self,
        cursor: str,
        *,
        block_milliseconds: int,
    ) -> WakeupBatch: ...


@runtime_checkable
class WakeupBroadcastPort(Protocol):
    """Broadcast consumption: every listener sees every hint.

    The SSE mode — each stream_source/chat listener holds its own cursor and
    wakes on any hint, leaving the kernel's consumer group untouched.
    """

    async def read_broadcast(
        self,
        cursor: str,
        *,
        block_milliseconds: int,
    ) -> WakeupBatch: ...


@runtime_checkable
class HintPublisherPort(Protocol):
    async def publish(self, channel: str, payload: str) -> RedisConnectivity: ...


@runtime_checkable
class HintStreamPort(Protocol):
    async def poll(self, *, timeout_seconds: float) -> HintPoll: ...


@runtime_checkable
class HintStreamFactoryPort(Protocol):
    def open(self, channel: str) -> AbstractAsyncContextManager[HintStreamPort]: ...


@runtime_checkable
class SessionListPublisher(Protocol):
    async def publish_changed(self) -> RedisConnectivity: ...


@runtime_checkable
class SessionListStreamFactory(Protocol):
    def open(self) -> AbstractAsyncContextManager[HintStreamPort]: ...


@runtime_checkable
class NotificationPublisher(Protocol):
    async def publish(self, user_id: str, payload: str) -> RedisConnectivity: ...


@runtime_checkable
class NotificationStreamFactory(Protocol):
    def open(self, user_id: str) -> AbstractAsyncContextManager[HintStreamPort]: ...


@runtime_checkable
class RuntimePolicyHintPublisher(Protocol):
    async def publish_changed(self, head_version: int) -> RedisConnectivity: ...


@runtime_checkable
class RuntimePolicyHintStreamFactory(Protocol):
    def open(self) -> AbstractAsyncContextManager[HintStreamPort]: ...
