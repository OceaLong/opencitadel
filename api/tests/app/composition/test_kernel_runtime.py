from __future__ import annotations

import importlib
import sys

import pytest

from app.execution_kernel import ExecutionKernelRuntime
from app.infrastructure.external.sandbox.factory import SandboxFactory
from app.runtime_role import ProcessRole
from tests.app.composition.test_api_runtime import (
    TEST_SETTINGS,
    _PolicyRepository,
    _resource_factories,
)


@pytest.mark.asyncio
async def test_kernel_runtime_contains_execution_services_only(monkeypatch) -> None:
    import app.infrastructure.security.cookie as cookie_module
    import app.infrastructure.security.csrf as csrf_module
    import app.infrastructure.security.oauth_clients as oauth_module
    from app.composition.kernel import open_kernel_runtime

    def reject_api_security(*_args, **_kwargs):
        raise AssertionError("kernel constructed an API presentation security collaborator")

    monkeypatch.setattr(cookie_module, "AuthCookieManager", reject_api_security)
    monkeypatch.setattr(csrf_module, "CsrfService", reject_api_security)
    monkeypatch.setattr(oauth_module, "OAuthClients", reject_api_security)

    events: list[str] = []
    async with open_kernel_runtime(
        TEST_SETTINGS,
        factories=_resource_factories(events),
        runtime_policy_repository_factory=lambda _resources: _PolicyRepository(),
    ) as runtime:
        assert isinstance(runtime.execution, ExecutionKernelRuntime)
        assert isinstance(runtime.sandbox_factory, SandboxFactory)
        assert runtime.resources.role is ProcessRole.EXECUTION_KERNEL
        assert runtime.scheduler_service._policy_reader is runtime.policy_reader
        assert runtime.execution.activity_registry.registered_types == (
            "child_run.start",
            "codebase.build",
            "knowledge.build",
            "model.call",
            "patrol.execute",
            "patrol.validate",
            "remediation.execute",
            "retrieval.search",
            "tool.call",
        )
        assert not hasattr(runtime, "auth_service")
        assert not hasattr(runtime, "oauth_registry")
        assert runtime.readiness.ready is True
        assert runtime.supervisor.pending_names == (
            "scheduler",
            "sandbox-pool",
            "sandbox-maintenance",
        )

    assert runtime.readiness.ready is False
    assert runtime.supervisor.pending_names == ()
    assert events[-3:] == ["storage:stop", "redis:stop", "postgres:stop"]


def test_kernel_module_cold_import_does_not_load_settings(monkeypatch) -> None:
    import core.config as config

    def fail_if_loaded():
        raise AssertionError("settings loaded during execution kernel import")

    monkeypatch.setattr(config, "load_deployment_settings", fail_if_loaded)
    monkeypatch.setattr(config, "load_deployment_settings", fail_if_loaded)
    sys.modules.pop("app.execution_kernel_main", None)

    module = importlib.import_module("app.execution_kernel_main")

    assert callable(module.main)
    assert callable(module.run_kernel)
