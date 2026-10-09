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
@pytest.mark.parametrize("observe_storage", [False, True])
async def test_kernel_runtime_contains_execution_services_only(
    monkeypatch, observe_storage
) -> None:
    import app.infrastructure.security.cookie as cookie_module
    import app.infrastructure.security.csrf as csrf_module
    import app.infrastructure.security.oauth_clients as oauth_module
    from app.composition.kernel import open_kernel_runtime
    from app.domain.models.authorization import AuthorizationContext

    def reject_api_security(*_args, **_kwargs):
        raise AssertionError("kernel constructed an API presentation security collaborator")

    monkeypatch.setattr(cookie_module, "AuthCookieManager", reject_api_security)
    monkeypatch.setattr(csrf_module, "CsrfService", reject_api_security)
    monkeypatch.setattr(oauth_module, "OAuthClients", reject_api_security)

    async def fake_execution_guard(**kwargs):
        return object()

    monkeypatch.setattr("app.composition.kernel.build_evaluation_execution", fake_execution_guard)
    physical_starts = []

    async def physical_start(**kwargs):
        physical_starts.append(kwargs["settings"])

    monkeypatch.setattr(
        "app.composition.physical_budget.initialize_physical_policy", physical_start
    )
    environment_starts = []

    async def environment_start(**kwargs):
        environment_starts.append(kwargs["settings"])

    monkeypatch.setattr(
        "app.composition.environment_capacity.initialize_environment_capacity", environment_start
    )
    maintenance_actions = []

    async def idle_lane(*args, stop_event, **kwargs):
        if args and callable(args[0]):
            maintenance_actions.append(args[0])
        await stop_event.wait()

    # Composition tests use intentionally nonfunctional storage; exercise loop
    # ownership/shutdown here and actual storage behavior in integration tests.
    monkeypatch.setattr("app.composition.kernel.run_scheduler_loop", idle_lane)
    monkeypatch.setattr("app.composition.kernel.run_maintenance_loop", idle_lane)
    monkeypatch.setattr("app.application.evaluation.runtime.EvaluationRuntime.run", idle_lane)
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )

    maintenance = []

    def capture_maintenance(**kwargs):
        instance = ArtifactProvenanceMaintenance(**kwargs)
        maintenance.append((instance, kwargs["handler"]))
        return instance

    monkeypatch.setattr("app.composition.kernel.ArtifactProvenanceMaintenance", capture_maintenance)
    events: list[str] = []
    observed_storage = []

    def observer(storage):
        observed_storage.append(storage)
        return storage

    async with open_kernel_runtime(
        TEST_SETTINGS,
        factories=_resource_factories(events),
        runtime_policy_repository_factory=lambda _resources: _PolicyRepository(),
        object_storage_wrapper=observer if observe_storage else None,
    ) as runtime:
        assert len(observed_storage) == int(observe_storage)
        assert physical_starts == [TEST_SETTINGS]
        assert environment_starts == [TEST_SETTINGS]
        assert isinstance(runtime.execution, ExecutionKernelRuntime)
        assert (
            runtime.execution.activity_registry.resolve(
                "model.call"
            )._execution_usage.physical_dispatch
            is not None
        )
        assert maintenance[0][1] is runtime.execution
        assert callable(maintenance[0][1].handle)
        assert runtime.execution._activities._content_writer is not None
        assert isinstance(runtime.sandbox_factory, SandboxFactory)
        assert runtime.resources.role is ProcessRole.EXECUTION_KERNEL
        assert runtime.scheduler_service._policy_reader is runtime.policy_reader
        assert runtime.execution.activity_registry.registered_types == (
            "child_run.start",
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
        from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView

        cleanup = [
            action
            for action in maintenance_actions
            if getattr(action, "__name__", "") == "cleanup_expired"
        ]
        comparison_lanes = [
            action
            for action in maintenance_actions
            if type(getattr(action, "__self__", None)).__name__ == "ComparisonDiffWorker"
        ]
        assert len(comparison_lanes) == 1
        assert len(cleanup) == 1
        assert isinstance(cleanup[0].__self__, PostgresExecutionView)
        assert cleanup[0].__self__.authorization == AuthorizationContext.system("execution-kernel")
        assert runtime.supervisor.pending_names == (
            "evaluation-scheduler",
            "evaluation-reconciler",
            "evaluation-scoring",
            "evaluation-cleanup",
            "scheduler",
            "execution-export",
            "execution-export-cleanup",
            "comparison-artifact-diff",
            "notification-delivery",
            "patrol-recheck",
            "execution-recovery",
            "artifact-provenance",
            "execution-usage",
            "execution-view-cache",
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


def test_evaluation_execution_guard_is_shared_by_command_and_activity_boundaries():
    from app.application.execution.activity_registry import ActivityRegistry
    from app.composition.kernel_runtime import build_execution_kernel_runtime
    from app.domain.models.authorization import AuthorizationContext

    sentinel = object()
    runtime = build_execution_kernel_runtime(
        session_factory=object(),
        redis=object(),
        authorization=AuthorizationContext.system("execution-kernel"),
        activity_registry=ActivityRegistry(),
        evaluation_execution=sentinel,
    )
    assert runtime._activities._execution_gate is sentinel
    assert runtime._command_handler._evaluation_execution is sentinel
