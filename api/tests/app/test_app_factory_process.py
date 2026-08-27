from __future__ import annotations

import importlib
import sys
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.composition.types import ApiRuntime, RuntimeReadiness
from app.domain.models.health_status import HealthStatus
from core.config import DeploymentSettings


def test_importing_main_does_not_load_settings_or_create_an_app(monkeypatch) -> None:
    """A module import must not choose configuration or allocate an application."""
    import core.config as config

    def fail_if_loaded():
        raise AssertionError("settings loaded during app.main import")

    monkeypatch.setattr(config, "load_deployment_settings", fail_if_loaded)
    sys.modules.pop("app.main", None)

    module = importlib.import_module("app.main")

    assert callable(module.create_app)
    assert not hasattr(module, "app")


def test_app_lifespan_owns_runtime_and_serves_status() -> None:
    """Requests must see the lifespan runtime and shutdown must revoke it."""
    from app.main import create_app

    events: list[str] = []
    runtime = object.__new__(ApiRuntime)
    readiness = RuntimeReadiness()
    readiness.mark_ready()

    class Status:
        async def check_all(self):
            return [HealthStatus(service="postgres", status="ok")]

    class Skills:
        async def seed_builtin_skills(self) -> None:
            events.append("bootstrap")

    class Agent:
        async def shutdown(self) -> None:
            events.append("agent:close")

    object.__setattr__(runtime, "status_service", Status())
    object.__setattr__(runtime, "readiness", readiness)
    object.__setattr__(runtime, "skill_service", Skills())
    object.__setattr__(runtime, "uow_factory", lambda: None)
    object.__setattr__(runtime, "password_hasher", object())
    object.__setattr__(runtime, "agent_service", Agent())

    @asynccontextmanager
    async def runtime_factory(_settings, *, on_critical_failure=None):
        assert on_critical_failure is not None
        events.append("runtime:open")
        try:
            yield runtime
        finally:
            events.append("runtime:close")

    app = create_app(
        DeploymentSettings(env="test"),
        runtime_factory=runtime_factory,
    )
    with TestClient(app) as client:
        response = client.get("/api/status")
        assert response.status_code == 200
        assert app.state.runtime is runtime

    assert app.state.runtime is None
    assert events == ["runtime:open", "bootstrap", "agent:close", "runtime:close"]


def test_runtime_health_endpoints_separate_liveness_from_readiness() -> None:
    """Drain must revoke readiness without claiming that the process is dead."""
    from app.main import create_app

    runtime = object.__new__(ApiRuntime)
    readiness = RuntimeReadiness()

    class Skills:
        async def seed_builtin_skills(self) -> None:
            return None

    class Agent:
        async def shutdown(self) -> None:
            return None

    object.__setattr__(runtime, "readiness", readiness)
    object.__setattr__(runtime, "skill_service", Skills())
    object.__setattr__(runtime, "uow_factory", lambda: None)
    object.__setattr__(runtime, "password_hasher", object())
    object.__setattr__(runtime, "agent_service", Agent())

    @asynccontextmanager
    async def runtime_factory(_settings, *, on_critical_failure=None):
        readiness.mark_ready()
        try:
            yield runtime
        finally:
            readiness.mark_not_ready()

    app = create_app(DeploymentSettings(env="test"), runtime_factory=runtime_factory)
    with TestClient(app) as client:
        assert client.get("/api/health/live").status_code == 200
        assert client.get("/api/health/ready").status_code == 200

        readiness.mark_not_ready()

        assert client.get("/api/health/ready").status_code == 503
        assert client.get("/api/health/live").status_code == 200
