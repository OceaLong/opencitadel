"""Repository boundaries for the deterministic acceptance plane."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
COMPOSE_PATH = REPOSITORY_ROOT / "docker-compose.yml"
EVIDENCE_SCHEMA_PATH = REPOSITORY_ROOT / "contracts/acceptance-evidence.schema.json"
CI_WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/ci.yml"

REQUIRED_ACCEPTANCE_IDS = frozenset(
    {
        "ID-LOGIN",
        "ID-LOGOUT",
        "ID-TEAM",
        "ID-INVITE",
        "ID-SCOPE",
        "ID-ANON",
        "INF-ENDPOINT",
        "INF-MODEL",
        "INF-PROBE",
        "INF-BIND",
        "INF-CAP",
        "INF-MISMATCH",
        "POL-EDIT",
        "POL-HISTORY",
        "POL-CAS",
        "POL-RESTORE",
        "KB-BUILD",
        "KB-PUBLISH",
        "KB-PIN",
        "KB-DEGRADED",
        "CB-BUILD",
        "CB-ARTIFACT",
        "CB-PIN",
        "CB-FAILSAFE",
        "RUN-AGENT",
        "RUN-ASK",
        "RUN-SSE",
        "RUN-APPROVE",
        "RUN-REJECT",
        "RUN-CANCEL",
        "PAT-PACK",
        "PAT-RUN",
        "PAT-EVIDENCE",
        "PAT-ADMISSION",
        "ADM-OVERVIEW",
        "ADM-GOVERNANCE",
        "ADM-AUDIT",
        "ADM-COMPLIANCE",
        "UI-MOBILE",
        "UI-KEYBOARD",
    }
)


def _compose_model() -> dict[str, object]:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_compose_uses_project_scoped_container_and_network_names() -> None:
    """Catch any service or network that reintroduces a global Docker identity."""
    compose = _compose_model()

    services = compose["services"]
    networks = compose["networks"]
    assert all("container_name" not in service for service in services.values())
    assert all("name" not in network for network in networks.values())


def test_all_sandbox_clients_share_the_broker_owned_dynamic_topology() -> None:
    """A run-scoped broker cannot accept clients using stale env-file identity."""
    compose = _compose_model()
    services = compose["services"]
    expected = {
        "SANDBOX_IMAGE": "${SANDBOX_IMAGE:-opencitadel-sandbox}",
        "SANDBOX_NAME_PREFIX": "${SANDBOX_NAME_PREFIX:-opencitadel-sandbox}",
        "SANDBOX_NETWORK": (
            "${SANDBOX_NETWORK:-${COMPOSE_PROJECT_NAME:-opencitadel}_opencitadel-sandbox-network}"
        ),
        "SANDBOX_LABELS": "${SANDBOX_LABELS:-{}}",
    }

    for service_name in (
        "opencitadel-sandbox-broker",
        "opencitadel-api",
        "opencitadel-execution-kernel",
    ):
        environment = services[service_name]["environment"]
        assert {key: environment[key] for key in expected} == expected


def test_acceptance_provider_is_profile_scoped_and_not_released() -> None:
    """Catch an acceptance-only image becoming a default or shipped runtime."""
    compose = _compose_model()
    services = compose["services"]
    assert "acceptance-inference" in services

    provider = services["acceptance-inference"]
    assert provider["profiles"] == ["acceptance"]
    assert "ports" not in provider
    assert provider["read_only"] is True
    assert provider["user"] == "1000:1000"
    assert provider["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in provider["security_opt"]
    assert provider["pids_limit"] == 64
    assert provider["mem_limit"] == "128m"
    assert provider["cpus"] == "0.25"
    assert provider.get("volumes") is None
    assert provider["networks"] == ["opencitadel-network"]

    provider_environment = provider["environment"]
    assert set(provider_environment) == {"ACCEPTANCE_PROVIDER_TOKEN"}
    assert provider_environment["ACCEPTANCE_PROVIDER_TOKEN"] == "${ACCEPTANCE_PROVIDER_TOKEN:-}"

    release_surfaces = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPOSITORY_ROOT / ".github/workflows").glob("*.yml"))
        if path.name != "ci.yml"
    )
    assert "acceptance-inference" not in release_surfaces
    assert "e2e/fixtures/inference-provider" not in release_surfaces


def test_evidence_schema_declares_the_complete_acceptance_matrix() -> None:
    """Catch required product journeys disappearing from machine evidence."""
    assert EVIDENCE_SCHEMA_PATH.is_file()
    schema = json.loads(EVIDENCE_SCHEMA_PATH.read_text(encoding="utf-8"))

    declared = schema["$defs"]["requirementId"]["enum"]
    assert len(declared) == len(set(declared))
    assert frozenset(declared) == REQUIRED_ACCEPTANCE_IDS


def test_ci_runs_the_disposable_acceptance_plane_and_always_uploads_evidence() -> None:
    text = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    job = workflow["jobs"]["acceptance-e2e"]

    assert "pull_request:" in text
    assert "branches: [main]" in text
    assert 1 <= int(job["timeout-minutes"]) <= 45
    assert "if" not in job

    steps = job["steps"]
    assert any("actions/setup-node@" in step.get("uses", "") for step in steps)
    assert any(step.get("with", {}).get("node-version") == "22" for step in steps)
    assert any("actions/setup-python@" in step.get("uses", "") for step in steps)
    assert any(step.get("with", {}).get("python-version") == "3.12" for step in steps)
    assert any("docker/setup-buildx-action@" in step.get("uses", "") for step in steps)

    commands = [str(step.get("run", "")).strip() for step in steps if "run" in step]
    assert any("pip install uv==0.11.19" in command for command in commands)
    assert any("stat -c '%g' /var/run/docker.sock" in command for command in commands)
    assert any("npm ci" in command for command in commands)
    assert any("playwright install --with-deps chromium" in command for command in commands)
    assert commands.count("./scripts/run-acceptance-e2e.sh --disposable") == 1
    assert "PATROL_E2E" not in "\n".join(commands)

    uploads = [step for step in steps if "actions/upload-artifact@" in step.get("uses", "")]
    assert len(uploads) == 1
    assert uploads[0].get("if") == "always()"
    assert uploads[0]["with"]["path"] == "tmp/acceptance/"
    assert uploads[0]["with"]["if-no-files-found"] == "error"
