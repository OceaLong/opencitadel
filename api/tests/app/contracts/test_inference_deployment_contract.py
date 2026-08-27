"""Repository-level deployment and documentation closure for inference."""

from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]

RUNTIME_SURFACES = (
    ".env.example",
    "scripts/quickstart.sh",
    "e2e/patrol.spec.ts",
    "deploy/helm/opencitadel/values.yaml",
    "deploy/helm/opencitadel/templates/secret.yaml",
)

DOCUMENTATION_SURFACES = (
    "README.md",
    "README.zh-CN.md",
    "api/README.md",
    "api/README.zh-CN.md",
    "ui/README.md",
    "ui/README.zh-CN.md",
    "docs/README.md",
    "docs/README.zh-CN.md",
    "docs/DOCUMENTATION_INVENTORY.md",
    "docs/DOCUMENTATION_INVENTORY.zh-CN.md",
    "docs/MAINTENANCE_CHECKLIST.md",
    "docs/MAINTENANCE_CHECKLIST.zh-CN.md",
    "docs/architecture/config-source-governance.md",
    "docs/architecture/config-source-governance.zh-CN.md",
    "docs/architecture/security-model.md",
    "docs/architecture/security-model.zh-CN.md",
    "docs/operations/ops-patrol.md",
    "docs/operations/ops-patrol.zh-CN.md",
    "docs/operations/deployment.md",
    "docs/operations/deployment.zh-CN.md",
    "docs/tutorials/01-self-host-10-minutes.md",
    "docs/tutorials/01-self-host-10-minutes.zh-CN.md",
    "docs/tutorials/06-ops-patrol.md",
    "docs/tutorials/06-ops-patrol.zh-CN.md",
    "docs/tutorials/07-approved-remediation.md",
    "docs/tutorials/07-approved-remediation.zh-CN.md",
    "docs/tutorials/08-ten-minute-governance-demo.md",
    "docs/tutorials/08-ten-minute-governance-demo.zh-CN.md",
)


def _source(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("relative_path", RUNTIME_SURFACES)
def test_runtime_surfaces_have_no_legacy_inference_or_feature_flag_controls(
    relative_path: str,
) -> None:
    source = _source(relative_path)

    assert "DEMO_LLM_" not in source
    assert "EMBEDDING_API_KEY" not in source
    assert "embeddingApiKey" not in source
    assert "feature_flags" not in source
    assert "enable_ops_patrol" not in source


def test_demo_seed_and_patrol_controls_use_canonical_names() -> None:
    environment = _source(".env.example")
    quickstart = _source("scripts/quickstart.sh")
    e2e = _source("e2e/patrol.spec.ts")
    helm_values = _source("deploy/helm/opencitadel/values.yaml")

    for name in (
        "DEMO_INFERENCE_BASE_URL",
        "DEMO_INFERENCE_CREDENTIAL",
        "DEMO_INFERENCE_MODEL",
        "DEMO_INFERENCE_PROVIDER",
    ):
        assert name in environment
        assert name in quickstart
    assert "/runtime-policies/operations/revisions" in e2e
    assert "patrol_policy:" not in helm_values


@pytest.mark.parametrize("relative_path", DOCUMENTATION_SURFACES)
def test_primary_documentation_has_no_removed_control_plane_terms(
    relative_path: str,
) -> None:
    source = _source(relative_path)

    assert "llm-endpoints-and-models" not in source
    assert "/llm-endpoints" not in source
    assert "/llm-models" not in source
    assert "llm_model_preferences" not in source
    assert "DEMO_LLM_" not in source
    assert "feature_flags" not in source
    assert "enable_ops_patrol" not in source
