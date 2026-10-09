"""Pure concurrent lifecycle checks; no services or physical work."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest


def test_full_kernel_shutdown_uses_actual_supervisor_and_preserves_failure():
    from scripts.execution_capacity.batch_runtime import with_kernel

    async def scenario(fail):
        stopped, entered = asyncio.Event(), asyncio.Event()
        runtime = SimpleNamespace(supervisor=SimpleNamespace(request_stop=stopped.set))

        @asynccontextmanager
        async def factory(settings):
            entered.set()
            yield runtime

        async def kernel(settings, *, runtime_factory):
            async with runtime_factory(settings):
                await stopped.wait()

        async def work():
            assert entered.is_set()
            if fail:
                raise ValueError("publication failed")
            return "actual receipt"

        return await with_kernel(None, factory, work, kernel=kernel)

    assert asyncio.run(scenario(False)) == "actual receipt"
    with pytest.raises(ValueError, match="publication failed"):
        asyncio.run(scenario(True))


def test_early_kernel_failure_does_not_leave_observer_waiting():
    from scripts.execution_capacity.batch_runtime import with_kernel

    @asynccontextmanager
    async def factory(settings):
        raise ValueError("startup failure")
        yield

    async def kernel(settings, *, runtime_factory):
        async with runtime_factory(settings):
            pass

    async def work():
        await asyncio.Event().wait()

    with pytest.raises(ValueError, match="startup failure"):
        asyncio.run(with_kernel(None, factory, work, kernel=kernel))
