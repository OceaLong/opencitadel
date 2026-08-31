from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.acceptance.manifest import (  # noqa: E402
    ACCEPTANCE_PROJECT_REQUIREMENTS,
    ArtifactInput,
    ComposeEvidence,
    CoverageEvidence,
    GitEvidence,
    ImageEvidence,
    ManifestInputs,
    MigrationEvidence,
    PlaywrightProjectEvidence,
    ResidueEvidence,
    ResultEvidence,
    SandboxEvidence,
    ScopeEvidence,
    ServiceEvidence,
    build_manifest,
    sha256_file,
    validate_manifest,
    write_manifest_atomic,
)

PRODUCTION_IMAGE_NAMES = (
    "api",
    "execution-kernel",
    "migrate",
    "ui",
    "sandbox",
    "ops-collector",
    "ops-actuator",
)
PLAYWRIGHT_PROJECT_NAMES = (
    "bootstrap",
    "identity",
    "control-plane",
    "resources",
    "execution",
    "patrol-admin",
    "cleanup",
)
SERVICE_NAMES = (
    "opencitadel-redis",
    "opencitadel-postgres",
    "opencitadel-minio",
    "opencitadel-sandbox-egress",
    "opencitadel-sandbox-broker",
    "opencitadel-migrate",
    "opencitadel-api",
    "opencitadel-execution-kernel",
    "opencitadel-ui",
    "opencitadel-ops-collector",
    "ops-console",
    "acceptance-inference",
    "opencitadel-nginx",
)


def _digest(seed: str) -> str:
    return f"sha256:{hashlib.sha256(seed.encode()).hexdigest()}"


def _manifest_inputs(evidence_root: Path) -> ManifestInputs:
    artifact_inputs = []
    for kind, relative in (
        ("junit", "playwright/junit.xml"),
        ("playwright-json", "playwright/results.json"),
        ("logs", "logs/stack.log"),
    ):
        path = evidence_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{kind} evidence\n", encoding="utf-8")
        artifact_inputs.append(ArtifactInput(kind=kind, path=path))

    return ManifestInputs(
        evidence_root=evidence_root,
        scope=ScopeEvidence(
            kind="full",
            requested_projects=PLAYWRIGHT_PROJECT_NAMES,
        ),
        result=ResultEvidence(
            status="passed",
            failure_reason=None,
            started_at="2026-08-27T01:02:03Z",
            finished_at="2026-08-27T01:04:03Z",
            duration_ms=120_000,
        ),
        git=GitEvidence(revision="a" * 40, dirty_tree_digest="b" * 64),
        compose=ComposeEvidence(
            project_name="opencitadel-acceptance-a",
            run_id="run-a",
            disposable=True,
        ),
        images=ImageEvidence(
            production={name: _digest(name) for name in PRODUCTION_IMAGE_NAMES},
            acceptance_provider=_digest("acceptance-provider"),
        ),
        migration=MigrationEvidence(alembic_head="202608270001"),
        services=tuple(
            ServiceEvidence(
                name=name,
                health="completed" if name == "opencitadel-migrate" else "healthy",
                restart_count=0,
                ready_at="2026-08-27T01:03:03Z",
            )
            for name in SERVICE_NAMES
        ),
        playwright_projects=tuple(
            PlaywrightProjectEvidence(
                name=name,
                tests=1,
                passed=1,
                failed=0,
                skipped=0,
                duration_ms=1_000,
            )
            for name in PLAYWRIGHT_PROJECT_NAMES
        ),
        coverage=tuple(
            CoverageEvidence(
                requirement_id=requirement_id,
                test_id=f"acceptance::{index:02d}",
                project=project,
                status="passed",
            )
            for index, (project, requirement_id) in enumerate(
                (project, requirement_id)
                for project, requirements in ACCEPTANCE_PROJECT_REQUIREMENTS.items()
                for requirement_id in sorted(requirements)
            )
        ),
        sandboxes=SandboxEvidence(created=2, drained=2),
        residue=ResidueEvidence(
            containers=0,
            networks=0,
            dynamic_sandboxes=0,
            volumes=0,
            retained_volumes=(),
        ),
        artifacts=tuple(artifact_inputs),
    )


def _valid_manifest(tmp_path: Path) -> tuple[dict[str, object], Path]:
    evidence_root = tmp_path / "evidence"
    return build_manifest(_manifest_inputs(evidence_root)), evidence_root


def test_build_manifest_hashes_artifacts_and_validates_complete_success(tmp_path: Path) -> None:
    manifest, evidence_root = _valid_manifest(tmp_path)

    assert manifest["schema_version"] == 1
    assert manifest["scope"] == {
        "kind": "full",
        "requested_projects": sorted(PLAYWRIGHT_PROJECT_NAMES),
    }
    assert manifest["playwright"]["totals"] == {
        "tests": 7,
        "passed": 7,
        "failed": 0,
        "skipped": 0,
        "duration_ms": 7_000,
    }
    first_artifact = manifest["artifacts"][0]
    assert not Path(first_artifact["path"]).is_absolute()
    assert first_artifact["sha256"] == sha256_file(evidence_root / first_artifact["path"])
    assert validate_manifest(manifest, evidence_root) == []


def test_validate_manifest_accepts_complete_partial_project_scope(tmp_path: Path) -> None:
    inputs = _manifest_inputs(tmp_path / "evidence")
    identity_ids = ACCEPTANCE_PROJECT_REQUIREMENTS["identity"]
    partial = ManifestInputs(
        **{
            **{field: getattr(inputs, field) for field in inputs.__dataclass_fields__},
            "scope": ScopeEvidence(kind="partial", requested_projects=("identity",)),
            "coverage": tuple(
                item for item in inputs.coverage if item.requirement_id in identity_ids
            ),
        }
    )
    manifest = build_manifest(partial)

    assert validate_manifest(manifest, partial.evidence_root) == []
    manifest["coverage"].pop()
    assert any(
        "missing acceptance requirements" in error
        for error in validate_manifest(manifest, partial.evidence_root)
    )


def test_validate_manifest_rejects_missing_and_duplicate_coverage(tmp_path: Path) -> None:
    manifest, evidence_root = _valid_manifest(tmp_path)
    missing = copy.deepcopy(manifest)
    missing["coverage"].pop()
    duplicate = copy.deepcopy(manifest)
    duplicate["coverage"].append(copy.deepcopy(duplicate["coverage"][0]))

    assert any(
        "missing acceptance requirements" in error
        for error in validate_manifest(missing, evidence_root)
    )
    assert any(
        "duplicate acceptance requirements" in error
        for error in validate_manifest(duplicate, evidence_root)
    )


def test_validate_manifest_rejects_skips_restarts_and_runtime_residue(tmp_path: Path) -> None:
    manifest, evidence_root = _valid_manifest(tmp_path)
    manifest["playwright"]["projects"][1]["passed"] = 0
    manifest["playwright"]["projects"][1]["skipped"] = 1
    manifest["playwright"]["totals"]["passed"] = 6
    manifest["playwright"]["totals"]["skipped"] = 1
    manifest["services"][0]["restart_count"] = 1
    manifest["residue"]["containers"] = 1

    errors = validate_manifest(manifest, evidence_root)
    assert any("zero skipped tests" in error for error in errors)
    assert any("unexpected service restarts" in error for error in errors)
    assert any("container residue" in error for error in errors)


def test_validate_manifest_rejects_missing_or_leaked_production_images(tmp_path: Path) -> None:
    manifest, evidence_root = _valid_manifest(tmp_path)
    manifest["images"]["production"].pop("api")
    manifest["images"]["production"]["acceptance-inference"] = _digest("leaked")

    errors = validate_manifest(manifest, evidence_root)
    assert any("production image set" in error for error in errors)
    assert any("acceptance provider leaked" in error for error in errors)


@pytest.mark.parametrize("unsafe_path", ["/tmp/junit.xml", "../junit.xml"])
def test_validate_manifest_rejects_artifact_paths_outside_evidence_root(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    manifest, evidence_root = _valid_manifest(tmp_path)
    manifest["artifacts"][0]["path"] = unsafe_path

    assert any("artifact path" in error for error in validate_manifest(manifest, evidence_root))


def test_validate_manifest_rejects_bad_artifact_hash(tmp_path: Path) -> None:
    manifest, evidence_root = _valid_manifest(tmp_path)
    manifest["artifacts"][0]["sha256"] = "0" * 64

    assert any(
        "artifact hash mismatch" in error for error in validate_manifest(manifest, evidence_root)
    )


def test_failed_manifest_remains_structurally_valid_after_clean_cleanup(tmp_path: Path) -> None:
    inputs = _manifest_inputs(tmp_path / "evidence")
    inputs = ManifestInputs(
        **{
            **{field: getattr(inputs, field) for field in inputs.__dataclass_fields__},
            "result": ResultEvidence(
                status="failed",
                failure_reason="playwright project failed",
                started_at=inputs.result.started_at,
                finished_at=inputs.result.finished_at,
                duration_ms=inputs.result.duration_ms,
            ),
            "coverage": (),
            "playwright_projects": (
                PlaywrightProjectEvidence(
                    name="bootstrap",
                    tests=1,
                    passed=0,
                    failed=1,
                    skipped=0,
                    duration_ms=1_000,
                ),
            ),
        }
    )
    manifest = build_manifest(inputs)

    assert validate_manifest(manifest, inputs.evidence_root) == []


def test_write_manifest_atomic_preserves_previous_file_on_serialization_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"stable":true}\n', encoding="utf-8")

    with pytest.raises(TypeError):
        write_manifest_atomic(path, {"invalid": {"not-json"}})

    assert path.read_text(encoding="utf-8") == '{"stable":true}\n'
    assert list(tmp_path.glob(".manifest.json.*.tmp")) == []
