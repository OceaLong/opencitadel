"""Release side effects must follow validation and publish Helm-compatible tags."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def _release_jobs() -> dict:
    workflow = REPOSITORY_ROOT / ".github/workflows/release.yml"
    return yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]


def _ancestors(jobs: dict, job_name: str) -> set[str]:
    dependencies: set[str] = set()
    pending = [job_name]
    while pending:
        needs = jobs[pending.pop()].get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        for dependency in needs:
            if dependency not in dependencies:
                dependencies.add(dependency)
                pending.append(dependency)
    return dependencies


@pytest.mark.parametrize("job_name", ["build-push", "github-release"])
def test_release_side_effects_require_chart_and_scan_gates(job_name: str) -> None:
    jobs = _release_jobs()

    assert {"chart-version-guard", "build-scan"} <= _ancestors(jobs, job_name)
    # An unconditional status expression would bypass failed prerequisites.
    for name in {job_name, *_ancestors(jobs, job_name)}:
        assert jobs[name].get("if", "success()") in ("success()", "${{ success() }}")
        assert not jobs[name].get("continue-on-error", False)


def test_release_publishes_version_tag_used_by_default_helm_images() -> None:
    steps = _release_jobs()["build-push"]["steps"]
    metadata = next(step for step in steps if step.get("id") == "meta")
    tags = {line.strip() for line in metadata["with"]["tags"].splitlines()}

    # ref tags retain the leading v; Chart.appVersion and default Helm tags do not.
    assert "type=semver,pattern={{version}}" in tags
    assert "type=ref,event=tag" in tags
    publish = next(step for step in steps if step.get("id") == "build")
    assert publish["with"]["tags"] == "${{ steps.meta.outputs.tags }}"


@pytest.mark.parametrize("matches", [True, False])
def test_chart_guard_accepts_only_matching_release_version(matches: bool) -> None:
    chart = yaml.safe_load((REPOSITORY_ROOT / "deploy/helm/opencitadel/Chart.yaml").read_text())
    version = str(chart["appVersion"]) if matches else "0.0.0-mismatched-release"
    guard = next(
        step["run"] for step in _release_jobs()["chart-version-guard"]["steps"] if "run" in step
    )
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", guard],
        cwd=REPOSITORY_ROOT,
        env={**os.environ, "GITHUB_REF_NAME": f"v{version}"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert (result.returncode == 0) is matches, result.stderr
