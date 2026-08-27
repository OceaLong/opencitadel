import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

import core.config as deployment_config
from app.infrastructure.models.registry import model_metadata
from tests.app.openapi_test_support import app


@pytest.mark.parametrize(
    "module_name",
    [
        "app.domain.config_port",
        "app.application.services.config_provider",
        "app.application.services.app_config_service",
        "app.interfaces.endpoints.app_config_routes",
    ],
)
def test_retired_runtime_config_modules_are_not_importable(module_name: str) -> None:
    assert importlib.util.find_spec(module_name) is None


def test_greenfield_schema_and_http_surface_exclude_app_config() -> None:
    assert {"app_configs", "app_config_revisions"}.isdisjoint(model_metadata.tables)
    assert not any(path.startswith("/api/app-config") for path in app.openapi()["paths"])
    assert not Path("config.yaml").exists()


def test_deployment_settings_have_no_legacy_aliases() -> None:
    deployment_settings = deployment_config.DeploymentSettings

    assert not hasattr(deployment_config, "Settings")
    assert not hasattr(deployment_config, "get_settings")
    assert "app_config_filepath" not in deployment_settings.model_fields
    assert "use_db_app_config" not in deployment_settings.model_fields


def test_runtime_policy_has_a_clean_process_import_boundary() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.domain.runtime_policy import ExecutionPolicy, policy_digest; print(policy_digest(1, ExecutionPolicy()))",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_alembic_uses_deployment_settings_without_legacy_aliases() -> None:
    source = Path("alembic/env.py").read_text()

    assert "load_deployment_settings" in source
    assert "get_deployment_settings" not in source
    assert "get_settings" not in source


def test_sandbox_runtime_has_no_module_global_behavior_settings() -> None:
    assert not Path("app/infrastructure/external/runtime_settings.py").exists()
    assert "sandbox_memory_probe_source" not in deployment_config.DeploymentSettings.model_fields

    forbidden = (
        "get_sandbox_runtime_settings",
        "get_admission_runtime_settings",
        "configure_sandbox_runtime",
        "configure_admission_runtime",
        "get_sandbox_pool",
        "get_sandbox_quota",
    )
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("app/infrastructure/external/sandbox").glob("*.py")
    )
    assert not any(name in sources for name in forbidden)
