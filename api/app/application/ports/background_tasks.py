from collections.abc import Awaitable, Callable
from typing import Protocol

BackgroundTaskFactory = Callable[[], Awaitable[None]]


class BackgroundTaskSupervisorPort(Protocol):
    async def start_transient(
        self,
        name: str,
        factory: BackgroundTaskFactory,
    ) -> None: ...


__all__ = ["BackgroundTaskFactory", "BackgroundTaskSupervisorPort"]
