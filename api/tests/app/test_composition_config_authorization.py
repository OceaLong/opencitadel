from dataclasses import fields
from types import SimpleNamespace

from app.composition.shared import fixture_replay_enabled
from app.composition.types import ApiRuntime, RuntimeReadiness


def test_api_runtime_schema_excludes_kernel_only_lifecycle() -> None:
    names = {field.name for field in fields(ApiRuntime)}

    assert {"auth_service", "runtime_policy_reader", "session_streams"} <= names
    assert "execution_kernel" not in names
    assert "scheduler_loop" not in names
    assert "sandbox_maintenance" not in names
    assert "patrol_retention_service" not in names


def test_production_composition_cannot_enable_fixture_replay() -> None:
    assert not fixture_replay_enabled(
        SimpleNamespace(env="production", patrol_fixture_replay_enabled=True)
    )
    assert fixture_replay_enabled(SimpleNamespace(env="test", patrol_fixture_replay_enabled=True))


def test_runtime_readiness_can_be_revoked_during_shutdown() -> None:
    readiness = RuntimeReadiness()

    readiness.mark_ready()
    assert readiness.ready is True

    readiness.mark_not_ready()
    assert readiness.ready is False
