from __future__ import annotations

import asyncio
from collections.abc import Awaitable


async def finish_snapshot_before_cancellation[T](awaitable: Awaitable[T]) -> T:
    """Finish one bounded SSE snapshot before propagating client cancellation.

    Cancelling an in-flight asyncpg operation invalidates its connection and can
    race the unit-of-work rollback.  SSE disconnects are expected, so keep only
    the current bounded snapshot attached until its database cleanup completes;
    the caller remains cancelled immediately afterwards.
    """

    task = asyncio.ensure_future(awaitable)
    cancellation: asyncio.CancelledError | None = None

    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancellation = cancellation or error

    result = task.result()
    if cancellation is not None:
        raise cancellation
    return result
