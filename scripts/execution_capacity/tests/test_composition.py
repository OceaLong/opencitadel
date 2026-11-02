"""Trusted composition checks; no resource initialization or network effects."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest


def binding():
    return {
        "environment": "test",
        "invocation": str(uuid4()),
        "fixture_id": str(uuid4()),
        "source_sha256": "a" * 64,
        "minio_endpoint": "owned:9000",
        "minio_bucket": "capacity",
        "team_id": None,
    }


def settings():
    return SimpleNamespace(
        env="test", storage_provider="minio", minio_endpoint="owned:9000", minio_bucket="capacity"
    )


def test_trusted_factory_installs_observation_before_connections_and_keeps_redis():
    from scripts.execution_capacity.composition import verified_factories

    from app.composition.resources import DEFAULT_RESOURCE_FACTORIES
    from app.infrastructure.storage.minio import Minio

    configured = verified_factories(settings(), binding())
    from app.infrastructure.execution.query_observation import install_query_observation

    assert configured.postgres(settings())._engine_observer is install_query_observation
    assert configured.redis is DEFAULT_RESOURCE_FACTORIES.redis
    assert isinstance(configured.storage(settings()), Minio)
    changed = settings()
    changed.minio_bucket = "foreign"
    with pytest.raises(ValueError, match="binding"):
        configured.storage(changed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("env", "production"),
        ("storage_provider", "cos"),
        ("minio_endpoint", "foreign:9000"),
        ("minio_bucket", "foreign"),
    ],
)
def test_factory_rejects_unbound_target_before_resource_construction(field, value):
    from scripts.execution_capacity.composition import verified_factories

    actual = settings()
    setattr(actual, field, value)
    with pytest.raises(ValueError, match="binding differs"):
        verified_factories(actual, binding())


def test_runtime_delegates_forward_complete_runtime_and_critical_callback(monkeypatch, tmp_path):
    from scripts.execution_capacity import composition

    seen = []
    sentinel = object()

    @asynccontextmanager
    async def complete(actual, **kwargs):
        seen.append((actual, kwargs))
        yield sentinel

    monkeypatch.setattr(composition, "open_api_runtime", complete)
    monkeypatch.setattr(composition, "open_kernel_runtime", complete)
    actual = settings()
    tmp_path.chmod(0o700)
    delegates = composition.runtime_factories(actual, binding(), writer_root=tmp_path)
    callback = object()

    async def exercise():
        async with delegates.api(actual, on_critical_failure=callback) as runtime:
            assert runtime is sentinel
        async with delegates.kernel(actual) as runtime:
            assert runtime is sentinel

    asyncio.run(exercise())
    assert seen[0][1]["on_critical_failure"] is callback
    assert seen[1][1]["on_critical_failure"] is None
    assert seen[0][1]["factories"] is seen[1][1]["factories"]


def test_normal_kernel_exposes_storage_wrapper_at_construction():
    import inspect

    from app.composition.kernel import open_kernel_runtime

    assert "object_storage_wrapper" in inspect.signature(open_kernel_runtime).parameters


def test_live_kernel_options_are_trusted_and_default_off():
    import inspect

    from app.composition.kernel import open_kernel_runtime
    from app.composition.kernel_runtime import build_execution_kernel_runtime

    assert inspect.signature(open_kernel_runtime).parameters["text_stream"].default is False
    assert "progress_sink_wrapper" in inspect.signature(build_execution_kernel_runtime).parameters


def test_capacity_resource_shutdown_keeps_inflight_listener_and_forbids_reuse():
    from sqlalchemy import create_engine

    from app.infrastructure.execution.query_observation import (
        capture_queries,
        install_query_observation,
    )
    from app.infrastructure.storage.postgres import Postgres

    engine = create_engine("postgresql+psycopg2://unused")
    facility = install_query_observation(engine)

    class AsyncEngine:
        async def dispose(self):
            await asyncio.sleep(0)

    resource = Postgres(settings(), engine_observer=install_query_observation)
    resource._engine = AsyncEngine()
    resource._observation_handles = [facility]

    async def run():
        with capture_queries(
            engine,
            sample_id="s",
            action_id="a",
            clock_id="c",
            clone_id="clone",
            expected=("steps.page",),
        ):
            await resource.shutdown()
            assert list(engine.dispatch.before_cursor_execute)
            assert list(engine.dispatch.after_cursor_execute)
        with pytest.raises(RuntimeError, match="single-use"):
            await resource.init()

    try:
        asyncio.run(run())
    finally:
        facility.close()  # test owns all users, known quiescent after asyncio.run
    assert Postgres(settings())._engine_observer is None
