"""Greenfield contracts for the single formal execution runtime."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.application.services.patrol_remediation_service import (
    PatrolRemediationService,
)
from app.application.services.patrol_run_service import PatrolRunService
from app.application.services.scheduled_job_service import ScheduledJobService
from app.execution_kernel import ExecutionKernelRuntime

API_ROOT = Path(__file__).parents[3]
REPO_ROOT = API_ROOT.parent

LEGACY_EXECUTION_SYMBOLS = (
    "AgentTaskRunner",
    "TaskRunnerFactory",
    "RedisStreamTask",
    "IngestionTaskRunner",
    "SessionEventModel",
    "session_events",
    "execution_facts",
    "execution_fact_",
    "execution_shadow",
    "FactSequencer",
    "legacy_dispatch",
    "SessionFactAdapter",
    "ResourceBuildFactAdapter",
    "PatrolFactAdapter",
    "AutomationFactAdapter",
    "ExecutionMode",
    "activity_firewall",
    "EventSequencePort",
    "PostgresEventSequenceAdapter",
    "event_sequence_port",
    "execution_session_event_probe",
)

LEGACY_EXECUTION_MODULES = (
    "app/application/services/task_runner_factory.py",
    "app/domain/services/agent_task_runner.py",
    "app/domain/services/knowledge_base/ingestion_task_runner.py",
    "app/infrastructure/external/task/redis_stream_task.py",
    "app/infrastructure/models/session_event.py",
    "app/infrastructure/execution/fact_models.py",
    "app/infrastructure/execution/shadow_models.py",
    "app/worker/execution_fact_sequencer_main.py",
    "app/worker/execution_dispatcher_main.py",
    "app/worker/execution_shadow_main.py",
    "app/worker/execution_shadow.py",
    "app/worker/execution_kernel.py",
    "app/worker/execution_kernel_main.py",
)


def test_formal_kernel_constructor_has_no_migration_bridge_ports() -> None:
    """Reintroducing Fact sequencing or legacy dispatch would restore dual truth."""

    parameters = inspect.signature(ExecutionKernelRuntime).parameters

    assert tuple(parameters) == (
        "command_handler",
        "inbox_worker",
        "activity_worker",
        "decision_worker",
        "outbox_dispatcher",
        "timer_dispatcher",
        "projector",
        "owner_scopes",
        "metrics",
        "activity_registry",
    )


def test_formal_kernel_exposes_only_event_store_runtime_loops() -> None:
    """Bridge loops must not be callable from the authoritative kernel."""

    public_coroutines = {
        name
        for name, value in inspect.getmembers(
            ExecutionKernelRuntime,
            predicate=inspect.iscoroutinefunction,
        )
        if not name.startswith("_")
    }

    assert "run_fact_sequencer_once" not in public_coroutines
    assert "run_legacy_dispatch_once" not in public_coroutines
    assert {
        "handle",
        "refresh_metrics",
        "run_activities_once",
        "run_decisions_once",
        "run_outbox_once",
        "run_projector_once",
        "run_timers_once",
    }.issubset(public_coroutines)


def test_kernel_runtime_has_no_resource_construction_factory() -> None:
    """Resource-bound construction belongs to the outer composition layer."""

    import app.execution_kernel as kernel

    assert not hasattr(kernel, "create_execution_kernel")


def test_legacy_execution_modules_are_physically_absent() -> None:
    for relative_path in LEGACY_EXECUTION_MODULES:
        assert not (API_ROOT / relative_path).exists(), relative_path


def test_production_source_contains_no_legacy_execution_vocabulary() -> None:
    production = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((API_ROOT / "app").rglob("*.py"))
    )

    for symbol in LEGACY_EXECUTION_SYMBOLS:
        assert symbol not in production, symbol


@pytest.mark.parametrize(
    "construct_without_execution_dependencies",
    [
        lambda: KnowledgeBaseService(lambda: None, object),
        lambda: PatrolRunService(lambda: None),
        lambda: PatrolRemediationService(lambda: None),
        lambda: ScheduledJobService(lambda: None),
    ],
)
def test_execution_backed_services_cannot_be_partially_configured(
    construct_without_execution_dependencies,
) -> None:
    """Missing execution wiring must fail at startup, not on first request."""
    with pytest.raises(TypeError):
        construct_without_execution_dependencies()


def test_patrol_execution_has_no_non_dispatching_bypass() -> None:
    """Product records cannot exist without their authoritative formal Run."""

    assert "dispatch" not in inspect.signature(PatrolRunService.trigger_pack).parameters
    assert "dispatch" not in inspect.signature(PatrolRunService.replay_run).parameters
    assert "dispatch" not in inspect.signature(PatrolRemediationService.propose).parameters


def test_application_execution_has_no_infrastructure_or_sqlalchemy_imports() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((API_ROOT / "app/application/execution").rglob("*.py"))
    )

    assert "app.infrastructure" not in source
    assert "sqlalchemy" not in source
