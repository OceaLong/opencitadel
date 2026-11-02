"""Launch, drain, probe, CI, and documentation contracts for final runtimes."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

API_ROOT = Path(__file__).parents[3]
REPO_ROOT = API_ROOT.parent


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _dotenv(relative: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _read(relative).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value
    return values


def test_api_launcher_uses_factory_and_configured_drain_timeout() -> None:
    launcher = _read("api/run.sh")

    assert "app.main:create_app" in launcher
    assert "--factory" in launcher
    assert "app.main:app" not in launcher
    assert "${OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS:-90}" in launcher


def test_compose_and_helm_separate_health_and_cover_application_drain() -> None:
    compose = yaml.safe_load(_read("docker-compose.yml"))
    api = compose["services"]["opencitadel-api"]
    kernel = compose["services"]["opencitadel-execution-kernel"]
    assert "/api/health/ready" in " ".join(api["healthcheck"]["test"])
    assert api["stop_grace_period"] == "${OPENCITADEL_STOP_GRACE_PERIOD:-120s}"
    assert kernel["stop_grace_period"] == "${OPENCITADEL_STOP_GRACE_PERIOD:-120s}"
    for runtime in (api, kernel):
        assert runtime["environment"]["OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS"] == (
            "${OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS:-90}"
        )

    values = yaml.safe_load(_read("deploy/helm/opencitadel/values.yaml"))
    assert (
        values["shutdown"]["terminationGracePeriodSeconds"] > values["shutdown"]["timeoutSeconds"]
    )

    api_deployment = _read("deploy/helm/opencitadel/templates/deployment-api.yaml")
    kernel_deployment = _read("deploy/helm/opencitadel/templates/deployment-execution-kernel.yaml")
    # Effective timeout rendering, including legacy fractional seconds, is
    # exercised against real Helm manifests in test_helm_configuration.py.
    assert "path: /api/health/ready" in api_deployment
    assert "path: /api/health/live" in api_deployment
    for deployment in (api_deployment, kernel_deployment):
        assert (
            "terminationGracePeriodSeconds: {{ .Values.shutdown.terminationGracePeriodSeconds }}"
            in deployment
        )
        assert 'include "opencitadel.deploymentEnv" . | fromYaml' in deployment
        assert "range $key, $value := $env" in deployment


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


def test_compose_labels_every_owned_resource_with_project_and_run() -> None:
    compose = yaml.safe_load(_read("docker-compose.yml"))
    expected = {
        "com.opencitadel.acceptance.project": "${ACCEPTANCE_PROJECT_ID:-none}",
        "com.opencitadel.acceptance.run": "${ACCEPTANCE_RUN_ID:-none}",
    }

    assert compose["x-ownership-labels"] == expected
    for group in ("services", "volumes", "networks"):
        for name, resource in compose[group].items():
            assert resource.get("labels") == expected, f"{group}.{name} is missing ownership labels"


def test_acceptance_environment_uses_an_isolated_project_namespace() -> None:
    example = _dotenv(".env.example")
    acceptance = _dotenv(".env.e2e")

    assert example["COMPOSE_PROJECT_NAME"] == "opencitadel"
    # Bundled topology is owned by Compose; it is no longer duplicated in the
    # minimal installation inputs.
    assert "SANDBOX_NETWORK" not in example
    assert acceptance["COMPOSE_PROJECT_NAME"] == "opencitadel-acceptance"
    assert acceptance["COMPOSE_PROFILES"] == "local,demo,patrol,acceptance"
    assert acceptance["ACCEPTANCE_PROJECT_ID"] == "opencitadel-acceptance"
    assert acceptance["ACCEPTANCE_RUN_ID"] == "local"
    assert acceptance["SANDBOX_NAME_PREFIX"] == "opencitadel-acceptance-local-sandbox"
    assert acceptance["SANDBOX_NETWORK"] == ("opencitadel-acceptance_opencitadel-sandbox-network")
    assert acceptance["NGINX_PORT"] == "18088"
    assert acceptance["NGINX_HTTPS_PORT"] == "18443"
    assert acceptance["OPS_CONSOLE_PORT"] == "19099"
    assert "OUTBOUND_PRIVATE_HOST_ALLOWLIST" not in example
    assert acceptance["OUTBOUND_PRIVATE_HOST_ALLOWLIST"] == (
        "acceptance-inference,opencitadel-ops-collector"
    )
    assert {"8090", "8091"} <= set(acceptance["OUTBOUND_ALLOWED_PORTS"].split(","))


def test_two_compose_projects_render_distinct_owned_resources() -> None:
    def render(project: str) -> dict:
        environment = {
            **os.environ,
            "ACCEPTANCE_PROJECT_ID": project,
            "ACCEPTANCE_RUN_ID": "contract",
            "ACCEPTANCE_PROVIDER_TOKEN": "acceptance-provider-token",
            "SANDBOX_NETWORK": f"{project}_opencitadel-sandbox-network",
        }
        completed = subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                project,
                "--env-file",
                ".env.e2e",
                "--profile",
                "acceptance",
                "config",
                "--format",
                "json",
            ],
            cwd=REPO_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    first = render("opencitadel-contract-a")
    second = render("opencitadel-contract-b")

    assert first["name"] != second["name"]
    assert (
        first["networks"]["opencitadel-network"]["name"]
        != (second["networks"]["opencitadel-network"]["name"])
    )
    assert first["volumes"]["postgres_data"]["name"] != second["volumes"]["postgres_data"]["name"]
    assert (
        first["services"]["opencitadel-api"]["labels"]["com.opencitadel.acceptance.project"]
        == "opencitadel-contract-a"
    )


def test_minimal_compose_inputs_render_without_optional_ops_credentials(tmp_path) -> None:
    """Check the installed defaults and actual override precedence together."""
    env_file = tmp_path / "installation.env"
    inputs = {
        "COMPOSE_PROJECT_NAME": "opencitadel-config-contract",
        "COMPOSE_PROFILES": "local",
        "OPENCITADEL_ENV_FILE": str(env_file),
        "FRONTEND_BASE_URL": "http://localhost:9088",
        "NGINX_PORT": "9088",
        "POSTGRES_ADMIN_PASSWORD": "admin-password-for-config-contract",
        "POSTGRES_MIGRATION_PASSWORD": "migration-password-for-config-contract",
        "POSTGRES_PASSWORD": "application-password-for-config-contract",
        "POSTGRES_KERNEL_PASSWORD": "kernel-password-for-config-contract",
        "API_KEY_SECRET": "api-key-secret-for-config-contract-32",
        "AUDIT_SIGNING_KEY": "audit-secret-for-config-contract-32",
        "JWT_SECRET": "jwt-secret-for-config-contract-32",
        "SESSION_SECRET": "session-secret-for-config-contract-32",
        "SANDBOX_BROKER_TOKEN": "broker-token-for-config-contract-32",
        "SANDBOX_TOKEN_SEED": "sandbox-seed-for-config-contract-32",
        "REDIS_PASSWORD": "redis-password-for-config-contract",
        "MINIO_SECRET_KEY": "minio-password-for-config-contract",
    }
    env_file.write_text("\n".join(f"{key}={value}" for key, value in inputs.items()) + "\n")

    def render(**overrides):
        result = subprocess.run(
            ["docker", "compose", "--env-file", str(env_file), "config", "--format", "json"],
            cwd=REPO_ROOT,
            env={"PATH": os.environ["PATH"], **overrides},
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    baseline = render()
    services = baseline["services"]
    assert "opencitadel-minio" in services
    assert "opencitadel-ops-collector" not in services
    assert "opencitadel-ops-actuator" not in services
    for name in ("opencitadel-api", "opencitadel-execution-kernel", "opencitadel-migrate"):
        environment = services[name]["environment"]
        assert environment["POSTGRES_HOST"] == "opencitadel-postgres"
        assert environment["REDIS_HOST"] == "opencitadel-redis"
        assert environment["STORAGE_PROVIDER"] == "minio"
        assert environment["MINIO_ACCESS_KEY"] == "opencitadel"
        assert environment["FRONTEND_BASE_URL"] == "http://localhost:9088"
    assert services["opencitadel-api"]["environment"]["POSTGRES_USER"] == "opencitadel_app"
    assert services["opencitadel-execution-kernel"]["environment"]["POSTGRES_USER"] == (
        "opencitadel_execution_kernel_runtime"
    )
    assert services["opencitadel-migrate"]["environment"]["POSTGRES_ADMIN_USER"] == (
        "opencitadel_migration_runtime"
    )
    for name in ("opencitadel-api", "opencitadel-execution-kernel"):
        assert services[name]["stop_grace_period"] == "2m0s"
        assert services[name]["environment"]["SANDBOX_HTTP_PROXY"] == (
            "http://opencitadel-sandbox-egress:3128"
        )

    overridden = render(
        POSTGRES_HOST="external-db", STORAGE_PROVIDER="cos", SANDBOX_IMAGE="custom-sandbox"
    )
    for name in ("opencitadel-api", "opencitadel-execution-kernel"):
        environment = overridden["services"][name]["environment"]
        assert environment["POSTGRES_HOST"] == "external-db"
        assert environment["STORAGE_PROVIDER"] == "cos"
        assert environment["SANDBOX_IMAGE"] == "custom-sandbox"


def test_quickstart_pins_every_compose_call_to_its_validated_project() -> None:
    quickstart = _read("scripts/quickstart.sh")

    assert "COMPOSE_PROJECT_NAME must match" in quickstart
    assert 'COMPOSE_CMD=(docker compose --project-name "$COMPOSE_PROJECT")' in quickstart
    assert 'source "$ROOT/scripts/configure-local.sh"' in quickstart
    assert 'configure_local_env "$ROOT" "$DEMO_MODE"' in quickstart
    assert 'COMPOSE_PROJECT="$(config_value COMPOSE_PROJECT_NAME)"' in quickstart
    assert 'SANDBOX_NETWORK="$(config_value SANDBOX_NETWORK)"' in quickstart
    assert (
        'SANDBOX_NETWORK="${SANDBOX_NETWORK:-${COMPOSE_PROJECT}_opencitadel-sandbox-network}"'
        in quickstart
    )
    assert 'COMPOSE_CMD+=(--env-file "$CONFIG_FILE")' in quickstart
    assert "export SANDBOX_NETWORK" in quickstart
    assert "docker compose build opencitadel-sandbox" not in quickstart


def test_ci_delegates_stack_ownership_to_the_acceptance_runner() -> None:
    workflow = _read(".github/workflows/ci.yml")
    runner = _read("scripts/acceptance/runner.py")

    assert "./scripts/run-acceptance-e2e.sh --disposable" in workflow
    assert "docker compose --env-file .env.e2e build opencitadel-sandbox" not in workflow
    assert "--profile local --profile demo --profile patrol up -d --build" not in workflow
    assert '"opencitadel-sandbox",' in runner
    assert '[*self._compose, "build", *_BUILD_SERVICES]' in runner
    assert '[*self._compose, "up", "-d", "--no-build", "--wait"]' in runner


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
