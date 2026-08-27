"""Launch, drain, probe, CI, and documentation contracts for final runtimes."""

from __future__ import annotations

from pathlib import Path

import yaml

API_ROOT = Path(__file__).parents[3]
REPO_ROOT = API_ROOT.parent


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def test_api_launcher_uses_factory_and_configured_drain_timeout() -> None:
    launcher = _read("api/run.sh")

    assert "app.main:create_app" in launcher
    assert "--factory" in launcher
    assert "app.main:app" not in launcher
    assert "${OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS:-30}" in launcher


def test_compose_and_helm_separate_health_and_cover_application_drain() -> None:
    compose = yaml.safe_load(_read("docker-compose.yml"))
    api = compose["services"]["opencitadel-api"]
    kernel = compose["services"]["opencitadel-execution-kernel"]
    assert "/api/health/ready" in " ".join(api["healthcheck"]["test"])
    assert api["stop_grace_period"] == "45s"
    assert kernel["stop_grace_period"] == "45s"

    values = yaml.safe_load(_read("deploy/helm/opencitadel/values.yaml"))
    assert (
        values["shutdown"]["terminationGracePeriodSeconds"] > values["shutdown"]["timeoutSeconds"]
    )

    api_deployment = _read("deploy/helm/opencitadel/templates/deployment-api.yaml")
    kernel_deployment = _read("deploy/helm/opencitadel/templates/deployment-execution-kernel.yaml")
    assert "path: /api/health/ready" in api_deployment
    assert "path: /api/health/live" in api_deployment
    for deployment in (api_deployment, kernel_deployment):
        assert (
            "terminationGracePeriodSeconds: {{ .Values.shutdown.terminationGracePeriodSeconds }}"
            in deployment
        )
        assert "OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS" in deployment


def test_local_and_ci_quality_gates_run_final_runtime_contracts() -> None:
    makefile = _read("Makefile")
    workflow = _read(".github/workflows/ci.yml")

    for path in (
        "tests/app/contracts/test_explicit_composition_boundaries.py",
        "tests/app/contracts/test_runtime_deployment_contract.py",
        "tests/app/test_app_factory_process.py",
        "tests/app/test_execution_kernel_process.py",
    ):
        assert path in makefile
        assert path in workflow


def test_ci_builds_dynamic_sandbox_image_before_starting_e2e_stack() -> None:
    workflow = _read(".github/workflows/ci.yml")

    sandbox_build = "docker compose --env-file .env.e2e build opencitadel-sandbox"
    stack_start = "--profile local --profile demo --profile patrol up -d --build"

    assert sandbox_build in workflow
    assert workflow.index(sandbox_build) < workflow.index(stack_start)


def test_bilingual_architecture_docs_describe_only_final_composition_model() -> None:
    english = "\n".join(
        _read(path)
        for path in (
            "docs/architecture/overview.md",
            "docs/architecture/technical-decisions.md",
            "docs/architecture/execution-kernel.md",
            "docs/operations/deployment.md",
        )
    )
    chinese = "\n".join(
        _read(path)
        for path in (
            "docs/architecture/overview.zh-CN.md",
            "docs/architecture/technical-decisions.zh-CN.md",
            "docs/architecture/execution-kernel.zh-CN.md",
            "docs/operations/deployment.zh-CN.md",
        )
    )

    for content in (english, chinese):
        for marker in (
            "ApiRuntime",
            "KernelRuntime",
            "TaskSupervisor",
            "uow.commit()",
            "post-commit",
            "/api/health/ready",
            "/api/health/live",
            "userId + workspaceId",
        ):
            assert marker in content
        assert "dependency-injector" not in content
        assert "implicit commit" not in content.lower()
