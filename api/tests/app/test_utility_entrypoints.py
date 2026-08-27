from __future__ import annotations

import importlib
import sys

import pytest

from app.runtime_role import ProcessRole


@pytest.mark.parametrize(
    "module_name",
    [
        "app.migrate",
        "app.migrate_storage",
        "app.migrate_runtime_policy_seed",
        "app.seed_demo",
        "app.execution_kernel_health",
        "app.infrastructure.external.sandbox.broker",
    ],
)
def test_utility_entrypoint_import_does_not_load_settings(
    module_name: str,
    monkeypatch,
) -> None:
    import core.config as config

    def fail_if_loaded():
        raise AssertionError(f"{module_name} loaded settings during import")

    monkeypatch.setattr(config, "load_deployment_settings", fail_if_loaded)
    monkeypatch.setattr(config, "load_deployment_settings", fail_if_loaded)
    sys.modules.pop(module_name, None)

    importlib.import_module(module_name)


def test_process_roles_are_values_without_global_mutators() -> None:
    import app.runtime_role as runtime_role

    assert set(ProcessRole) == {
        ProcessRole.API,
        ProcessRole.EXECUTION_KERNEL,
        ProcessRole.MIGRATE,
        ProcessRole.SEED,
        ProcessRole.SANDBOX_BROKER,
    }
    assert not hasattr(runtime_role, "get_role")
    assert not hasattr(runtime_role, "set_role")
