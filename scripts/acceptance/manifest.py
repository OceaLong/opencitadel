"""Build and validate immutable acceptance evidence manifests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

ACCEPTANCE_PROJECT_REQUIREMENTS = {
    "bootstrap": frozenset(),
    "identity": frozenset({"ID-LOGIN", "ID-LOGOUT", "ID-TEAM", "ID-INVITE", "ID-SCOPE", "ID-ANON"}),
    "control-plane": frozenset(
        {
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
        }
    ),
    "resources": frozenset(
        {
            "KB-BUILD",
            "KB-PUBLISH",
            "KB-PIN",
            "KB-DEGRADED",
            "CB-BUILD",
            "CB-ARTIFACT",
            "CB-PIN",
            "CB-FAILSAFE",
        }
    ),
    "execution": frozenset(
        {"RUN-AGENT", "RUN-ASK", "RUN-SSE", "RUN-APPROVE", "RUN-REJECT", "RUN-CANCEL"}
    ),
    "patrol-admin": frozenset(
        {
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
    ),
    "cleanup": frozenset(),
}
REQUIRED_ACCEPTANCE_IDS = frozenset().union(*ACCEPTANCE_PROJECT_REQUIREMENTS.values())
ACCEPTANCE_REQUIREMENT_PROJECT = {
    requirement_id: project
    for project, requirement_ids in ACCEPTANCE_PROJECT_REQUIREMENTS.items()
    for requirement_id in requirement_ids
}
PRODUCTION_IMAGE_NAMES = frozenset(
    {
        "api",
        "execution-kernel",
        "migrate",
        "ui",
        "sandbox",
        "ops-collector",
        "ops-actuator",
    }
)
PLAYWRIGHT_PROJECT_NAMES = frozenset(ACCEPTANCE_PROJECT_REQUIREMENTS)
SERVICE_NAMES = frozenset(
    {
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
    }
)
_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "scope",
        "result",
        "git",
        "compose",
        "images",
        "migration",
        "services",
        "playwright",
        "coverage",
        "sandboxes",
        "residue",
        "artifacts",
    }
)
_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_REQUIRED_ARTIFACT_KINDS = frozenset({"junit", "playwright-json", "logs"})


@dataclass(frozen=True, slots=True)
class ResultEvidence:
    status: Literal["passed", "failed"]
    failure_reason: str | None
    started_at: str
    finished_at: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class ScopeEvidence:
    kind: Literal["full", "partial"]
    requested_projects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GitEvidence:
    revision: str
    dirty_tree_digest: str


@dataclass(frozen=True, slots=True)
class ComposeEvidence:
    project_name: str
    run_id: str
    disposable: bool


@dataclass(frozen=True, slots=True)
class ImageEvidence:
    production: Mapping[str, str]
    acceptance_provider: str


@dataclass(frozen=True, slots=True)
class MigrationEvidence:
    alembic_head: str


@dataclass(frozen=True, slots=True)
class ServiceEvidence:
    name: str
    health: Literal["not_started", "starting", "healthy", "unhealthy", "completed"]
    restart_count: int
    ready_at: str | None


@dataclass(frozen=True, slots=True)
class PlaywrightProjectEvidence:
    name: str
    tests: int
    passed: int
    failed: int
    skipped: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class CoverageEvidence:
    requirement_id: str
    test_id: str
    project: str
    status: Literal["passed", "failed", "skipped", "not_run"]


@dataclass(frozen=True, slots=True)
class SandboxEvidence:
    created: int
    drained: int


@dataclass(frozen=True, slots=True)
class ResidueEvidence:
    containers: int
    networks: int
    dynamic_sandboxes: int
    volumes: int
    retained_volumes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArtifactInput:
    kind: str
    path: Path


@dataclass(frozen=True, slots=True)
class ManifestInputs:
    evidence_root: Path
    scope: ScopeEvidence
    result: ResultEvidence
    git: GitEvidence
    compose: ComposeEvidence
    images: ImageEvidence
    migration: MigrationEvidence
    services: tuple[ServiceEvidence, ...]
    playwright_projects: tuple[PlaywrightProjectEvidence, ...]
    coverage: tuple[CoverageEvidence, ...]
    sandboxes: SandboxEvidence
    residue: ResidueEvidence
    artifacts: tuple[ArtifactInput, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_record(artifact: ArtifactInput, evidence_root: Path) -> dict[str, object]:
    root = evidence_root.resolve()
    path = artifact.path.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"artifact path is outside evidence root: {artifact.path}") from exc
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        "kind": artifact.kind,
        "path": relative.as_posix(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _playwright_totals(projects: Sequence[PlaywrightProjectEvidence]) -> dict[str, int]:
    return {
        key: sum(getattr(project, key) for project in projects)
        for key in ("tests", "passed", "failed", "skipped", "duration_ms")
    }


def build_manifest(inputs: ManifestInputs) -> dict[str, object]:
    projects = tuple(sorted(inputs.playwright_projects, key=lambda item: item.name))
    return {
        "schema_version": 1,
        "scope": {
            "kind": inputs.scope.kind,
            "requested_projects": sorted(inputs.scope.requested_projects),
        },
        "result": asdict(inputs.result),
        "git": asdict(inputs.git),
        "compose": asdict(inputs.compose),
        "images": {
            "production": dict(sorted(inputs.images.production.items())),
            "acceptance_provider": inputs.images.acceptance_provider,
        },
        "migration": asdict(inputs.migration),
        "services": [asdict(item) for item in sorted(inputs.services, key=lambda item: item.name)],
        "playwright": {
            "projects": [asdict(item) for item in projects],
            "totals": _playwright_totals(projects),
        },
        "coverage": [
            asdict(item) for item in sorted(inputs.coverage, key=lambda item: item.requirement_id)
        ],
        "sandboxes": asdict(inputs.sandboxes),
        "residue": {
            **asdict(inputs.residue),
            "retained_volumes": sorted(inputs.residue.retained_volumes),
        },
        "artifacts": sorted(
            (_artifact_record(item, inputs.evidence_root) for item in inputs.artifacts),
            key=lambda item: str(item["path"]),
        ),
    }


def _mapping(value: object, label: str, errors: list[str]) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        errors.append(f"{label} must be an object")
        return None
    return value


def _sequence(value: object, label: str, errors: list[str]) -> Sequence[object] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        errors.append(f"{label} must be an array")
        return None
    return value


def _check_keys(
    value: Mapping[str, object],
    expected: frozenset[str],
    label: str,
    errors: list[str],
) -> None:
    actual = frozenset(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        errors.append(f"{label} missing keys: {', '.join(missing)}")
    if extra:
        errors.append(f"{label} has unknown keys: {', '.join(extra)}")


def _nonnegative_int(value: object, label: str, errors: list[str]) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        errors.append(f"{label} must be a non-negative integer")
        return None
    return value


def _validate_result(document: Mapping[str, object], errors: list[str]) -> str | None:
    result = _mapping(document.get("result"), "result", errors)
    if result is None:
        return None
    _check_keys(
        result,
        frozenset({"status", "failure_reason", "started_at", "finished_at", "duration_ms"}),
        "result",
        errors,
    )
    status = result.get("status")
    if status not in {"passed", "failed"}:
        errors.append("result.status must be passed or failed")
        return None
    reason = result.get("failure_reason")
    if status == "passed" and reason is not None:
        errors.append("passed result must not have a failure reason")
    if status == "failed" and (not isinstance(reason, str) or not reason.strip()):
        errors.append("failed result requires a failure reason")
    for timestamp in ("started_at", "finished_at"):
        value = result.get(timestamp)
        if not isinstance(value, str) or not value.endswith("Z"):
            errors.append(f"result.{timestamp} must be an RFC3339 UTC timestamp")
    _nonnegative_int(result.get("duration_ms"), "result.duration_ms", errors)
    return status


def _validate_scope(
    document: Mapping[str, object], errors: list[str]
) -> tuple[str | None, frozenset[str]]:
    scope = _mapping(document.get("scope"), "scope", errors)
    if scope is None:
        return None, REQUIRED_ACCEPTANCE_IDS
    _check_keys(scope, frozenset({"kind", "requested_projects"}), "scope", errors)
    kind = scope.get("kind")
    if kind not in {"full", "partial"}:
        errors.append("scope.kind must be full or partial")
        kind = None
    requested_raw = _sequence(scope.get("requested_projects"), "scope.requested_projects", errors)
    if requested_raw is None:
        return kind, REQUIRED_ACCEPTANCE_IDS
    requested = [item for item in requested_raw if isinstance(item, str)]
    if len(requested) != len(requested_raw):
        errors.append("scope.requested_projects must contain only strings")
    requested_set = frozenset(requested)
    if not requested_set:
        errors.append("scope.requested_projects must not be empty")
    if len(requested) != len(requested_set):
        errors.append("scope.requested_projects must be unique")
    unknown = requested_set - PLAYWRIGHT_PROJECT_NAMES
    if unknown:
        errors.append(f"scope has unknown Playwright projects: {', '.join(sorted(unknown))}")
    if kind == "full" and requested_set != PLAYWRIGHT_PROJECT_NAMES:
        errors.append("full scope must request every Playwright project")
    if kind == "partial" and requested_set == PLAYWRIGHT_PROJECT_NAMES:
        errors.append("partial scope must omit at least one Playwright project")
    expected = frozenset().union(
        *(ACCEPTANCE_PROJECT_REQUIREMENTS.get(project, frozenset()) for project in requested_set)
    )
    return kind, expected


def _validate_git_compose_migration(document: Mapping[str, object], errors: list[str]) -> bool:
    disposable = False
    git = _mapping(document.get("git"), "git", errors)
    if git is not None:
        _check_keys(git, frozenset({"revision", "dirty_tree_digest"}), "git", errors)
        if not isinstance(git.get("revision"), str) or not _GIT_REVISION.fullmatch(git["revision"]):
            errors.append("git.revision must be a hexadecimal revision")
        if not isinstance(git.get("dirty_tree_digest"), str) or not _SHA256.fullmatch(
            git["dirty_tree_digest"]
        ):
            errors.append("git.dirty_tree_digest must be a SHA-256 digest")
    compose = _mapping(document.get("compose"), "compose", errors)
    if compose is not None:
        _check_keys(
            compose,
            frozenset({"project_name", "run_id", "disposable"}),
            "compose",
            errors,
        )
        errors.extend(
            f"compose.{key} must be a non-empty string"
            for key in ("project_name", "run_id")
            if not isinstance(compose.get(key), str) or not compose[key]
        )
        if not isinstance(compose.get("disposable"), bool):
            errors.append("compose.disposable must be a boolean")
        else:
            disposable = compose["disposable"]
    migration = _mapping(document.get("migration"), "migration", errors)
    if migration is not None:
        _check_keys(migration, frozenset({"alembic_head"}), "migration", errors)
        if not isinstance(migration.get("alembic_head"), str) or not migration["alembic_head"]:
            errors.append("migration.alembic_head must be a non-empty string")
    return disposable


def _validate_images(document: Mapping[str, object], errors: list[str]) -> None:
    images = _mapping(document.get("images"), "images", errors)
    if images is None:
        return
    _check_keys(images, frozenset({"production", "acceptance_provider"}), "images", errors)
    production = _mapping(images.get("production"), "images.production", errors)
    if production is not None:
        names = frozenset(production)
        if names != PRODUCTION_IMAGE_NAMES:
            errors.append("production image set must contain exactly the seven shipped images")
        if "acceptance-inference" in names:
            errors.append("acceptance provider leaked into production images")
        for name, digest in production.items():
            if not isinstance(digest, str) or not _IMAGE_DIGEST.fullmatch(digest):
                errors.append(f"production image {name} has an invalid digest")
    provider_digest = images.get("acceptance_provider")
    if not isinstance(provider_digest, str) or not _IMAGE_DIGEST.fullmatch(provider_digest):
        errors.append("acceptance provider has an invalid image digest")


def _validate_services(
    document: Mapping[str, object], status: str | None, errors: list[str]
) -> None:
    services = _sequence(document.get("services"), "services", errors)
    if services is None:
        return
    names: list[str] = []
    restarted: list[str] = []
    for index, raw in enumerate(services):
        service = _mapping(raw, f"services[{index}]", errors)
        if service is None:
            continue
        _check_keys(
            service,
            frozenset({"name", "health", "restart_count", "ready_at"}),
            f"services[{index}]",
            errors,
        )
        name = service.get("name")
        if isinstance(name, str):
            names.append(name)
        else:
            errors.append(f"services[{index}].name must be a string")
            name = f"index-{index}"
        health = service.get("health")
        if health not in {"not_started", "starting", "healthy", "unhealthy", "completed"}:
            errors.append(f"service {name} has invalid health")
        restart_count = _nonnegative_int(
            service.get("restart_count"), f"service {name} restart_count", errors
        )
        if restart_count:
            restarted.append(name)
        ready_at = service.get("ready_at")
        if ready_at is not None and (not isinstance(ready_at, str) or not ready_at.endswith("Z")):
            errors.append(f"service {name} has invalid ready_at")
        if status == "passed" and (health not in {"healthy", "completed"} or ready_at is None):
            errors.append(f"service {name} was not ready in a passed run")
    if len(names) != len(set(names)):
        errors.append("services contain duplicate names")
    if status == "passed" and frozenset(names) != SERVICE_NAMES:
        errors.append("passed manifest must contain the complete service set")
    if status == "passed" and restarted:
        errors.append(f"unexpected service restarts: {', '.join(sorted(restarted))}")


def _validate_playwright(
    document: Mapping[str, object], status: str | None, errors: list[str]
) -> None:
    playwright = _mapping(document.get("playwright"), "playwright", errors)
    if playwright is None:
        return
    _check_keys(playwright, frozenset({"projects", "totals"}), "playwright", errors)
    projects = _sequence(playwright.get("projects"), "playwright.projects", errors)
    totals = _mapping(playwright.get("totals"), "playwright.totals", errors)
    expected_totals = dict.fromkeys(("tests", "passed", "failed", "skipped", "duration_ms"), 0)
    names: list[str] = []
    if projects is not None:
        for index, raw in enumerate(projects):
            project = _mapping(raw, f"playwright.projects[{index}]", errors)
            if project is None:
                continue
            _check_keys(
                project,
                frozenset({"name", "tests", "passed", "failed", "skipped", "duration_ms"}),
                f"playwright.projects[{index}]",
                errors,
            )
            name = project.get("name")
            if isinstance(name, str):
                names.append(name)
            else:
                errors.append(f"playwright.projects[{index}].name must be a string")
            counts: dict[str, int] = {}
            for key in expected_totals:
                count = _nonnegative_int(
                    project.get(key), f"playwright.projects[{index}].{key}", errors
                )
                if count is not None:
                    counts[key] = count
                    expected_totals[key] += count
            if all(key in counts for key in ("tests", "passed", "failed", "skipped")) and counts[
                "tests"
            ] != (counts["passed"] + counts["failed"] + counts["skipped"]):
                errors.append(f"playwright project {name} test counts do not add up")
    if totals is not None:
        _check_keys(totals, frozenset(expected_totals), "playwright.totals", errors)
        for key, expected in expected_totals.items():
            if totals.get(key) != expected:
                errors.append(f"playwright total {key} does not match project totals")
    if len(names) != len(set(names)):
        errors.append("playwright projects contain duplicate names")
    if status == "passed":
        if frozenset(names) != PLAYWRIGHT_PROJECT_NAMES:
            errors.append("passed manifest must contain every Playwright project")
        if expected_totals["failed"] != 0:
            errors.append("passed manifest requires zero failed tests")
        if expected_totals["skipped"] != 0:
            errors.append("passed manifest requires zero skipped tests")


def _validate_coverage(
    document: Mapping[str, object],
    status: str | None,
    expected_requirements: frozenset[str],
    errors: list[str],
) -> None:
    coverage = _sequence(document.get("coverage"), "coverage", errors)
    if coverage is None:
        return
    requirement_ids: list[str] = []
    for index, raw in enumerate(coverage):
        item = _mapping(raw, f"coverage[{index}]", errors)
        if item is None:
            continue
        _check_keys(
            item,
            frozenset({"requirement_id", "test_id", "project", "status"}),
            f"coverage[{index}]",
            errors,
        )
        requirement_id = item.get("requirement_id")
        if not isinstance(requirement_id, str) or requirement_id not in REQUIRED_ACCEPTANCE_IDS:
            errors.append(f"coverage[{index}] has an unknown requirement")
        else:
            requirement_ids.append(requirement_id)
        if not isinstance(item.get("test_id"), str) or not item["test_id"]:
            errors.append(f"coverage[{index}].test_id must be non-empty")
        if item.get("project") not in PLAYWRIGHT_PROJECT_NAMES:
            errors.append(f"coverage[{index}] has an unknown Playwright project")
        elif isinstance(requirement_id, str) and requirement_id in ACCEPTANCE_REQUIREMENT_PROJECT:
            expected_project = ACCEPTANCE_REQUIREMENT_PROJECT[requirement_id]
            if item.get("project") != expected_project:
                errors.append(
                    f"coverage[{index}] requirement {requirement_id} belongs to {expected_project}"
                )
        if item.get("status") not in {"passed", "failed", "skipped", "not_run"}:
            errors.append(f"coverage[{index}] has an invalid status")
    duplicates = sorted(name for name, count in Counter(requirement_ids).items() if count > 1)
    if duplicates:
        errors.append(f"duplicate acceptance requirements: {', '.join(duplicates)}")
    if status == "passed":
        actual_requirements = frozenset(requirement_ids)
        missing = sorted(expected_requirements - actual_requirements)
        if missing:
            errors.append(f"missing acceptance requirements: {', '.join(missing)}")
        unexpected = sorted(actual_requirements - expected_requirements)
        if unexpected:
            errors.append(f"requirements outside requested scope: {', '.join(unexpected)}")
        if any(item.get("status") != "passed" for item in coverage if isinstance(item, Mapping)):
            errors.append("passed manifest requires every acceptance requirement to pass")


def _validate_sandboxes_and_residue(
    document: Mapping[str, object],
    status: str | None,
    disposable: bool,
    errors: list[str],
) -> None:
    sandboxes = _mapping(document.get("sandboxes"), "sandboxes", errors)
    if sandboxes is not None:
        _check_keys(sandboxes, frozenset({"created", "drained"}), "sandboxes", errors)
        created = _nonnegative_int(sandboxes.get("created"), "sandboxes.created", errors)
        drained = _nonnegative_int(sandboxes.get("drained"), "sandboxes.drained", errors)
        if (
            status == "passed"
            and created is not None
            and drained is not None
            and created != drained
        ):
            errors.append("passed manifest requires every dynamic sandbox to drain")
    residue = _mapping(document.get("residue"), "residue", errors)
    if residue is None:
        return
    _check_keys(
        residue,
        frozenset({"containers", "networks", "dynamic_sandboxes", "volumes", "retained_volumes"}),
        "residue",
        errors,
    )
    counts: dict[str, int] = {}
    for key in ("containers", "networks", "dynamic_sandboxes", "volumes"):
        value = _nonnegative_int(residue.get(key), f"residue.{key}", errors)
        if value is not None:
            counts[key] = value
    retained = _sequence(residue.get("retained_volumes"), "residue.retained_volumes", errors)
    retained_names = [] if retained is None else list(retained)
    if any(not isinstance(name, str) or not name for name in retained_names):
        errors.append("retained volume names must be non-empty strings")
    if len(retained_names) != len(set(retained_names)):
        errors.append("retained volume names must be unique")
    for key in ("containers", "networks", "dynamic_sandboxes"):
        if counts.get(key, 0) != 0:
            label = "container" if key == "containers" else key.replace("_", " ")
            errors.append(f"{label} residue must be zero")
    if disposable:
        if counts.get("volumes", 0) != 0 or retained_names:
            errors.append("disposable run must leave zero volume residue")
    elif counts.get("volumes") is not None and counts["volumes"] != len(retained_names):
        errors.append("retained volume names must account for local volume residue")


def _validate_artifacts(
    document: Mapping[str, object], evidence_root: Path, status: str | None, errors: list[str]
) -> None:
    artifacts = _sequence(document.get("artifacts"), "artifacts", errors)
    if artifacts is None:
        return
    kinds: list[str] = []
    paths: list[str] = []
    root = evidence_root.resolve()
    for index, raw in enumerate(artifacts):
        artifact = _mapping(raw, f"artifacts[{index}]", errors)
        if artifact is None:
            continue
        _check_keys(
            artifact,
            frozenset({"kind", "path", "sha256", "size_bytes"}),
            f"artifacts[{index}]",
            errors,
        )
        kind = artifact.get("kind")
        path_text = artifact.get("path")
        if isinstance(kind, str) and kind:
            kinds.append(kind)
        else:
            errors.append(f"artifacts[{index}].kind must be non-empty")
        if not isinstance(path_text, str) or not path_text:
            errors.append(f"artifact path at index {index} must be non-empty")
            continue
        paths.append(path_text)
        pure_path = PurePosixPath(path_text)
        if pure_path.is_absolute() or ".." in pure_path.parts:
            errors.append(f"artifact path escapes evidence root: {path_text}")
            continue
        path = (root / Path(*pure_path.parts)).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            errors.append(f"artifact path escapes evidence root: {path_text}")
            continue
        if not path.is_file():
            errors.append(f"artifact file is missing: {path_text}")
            continue
        digest = artifact.get("sha256")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            errors.append(f"artifact digest is invalid: {path_text}")
        elif sha256_file(path) != digest:
            errors.append(f"artifact hash mismatch: {path_text}")
        if artifact.get("size_bytes") != path.stat().st_size:
            errors.append(f"artifact size mismatch: {path_text}")
    if len(paths) != len(set(paths)):
        errors.append("artifact paths must be unique")
    if status == "passed" and not _REQUIRED_ARTIFACT_KINDS.issubset(kinds):
        errors.append("passed manifest is missing required evidence artifact kinds")


def validate_manifest(document: Mapping[str, object], evidence_root: Path) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, Mapping):
        return ["manifest must be an object"]
    _check_keys(document, _TOP_LEVEL_KEYS, "manifest", errors)
    if document.get("schema_version") != 1:
        errors.append("schema_version must equal 1")
    status = _validate_result(document, errors)
    _scope_kind, expected_requirements = _validate_scope(document, errors)
    disposable = _validate_git_compose_migration(document, errors)
    _validate_images(document, errors)
    _validate_services(document, status, errors)
    _validate_playwright(document, status, errors)
    _validate_coverage(document, status, expected_requirements, errors)
    _validate_sandboxes_and_residue(document, status, disposable, errors)
    _validate_artifacts(document, evidence_root, status, errors)
    return sorted(set(errors))


def write_manifest_atomic(path: Path, document: Mapping[str, object]) -> None:
    encoded = (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary.name, 0o600)
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


__all__ = [
    "REQUIRED_ACCEPTANCE_IDS",
    "ArtifactInput",
    "ComposeEvidence",
    "CoverageEvidence",
    "GitEvidence",
    "ImageEvidence",
    "ManifestInputs",
    "MigrationEvidence",
    "PlaywrightProjectEvidence",
    "ResidueEvidence",
    "ResultEvidence",
    "SandboxEvidence",
    "ServiceEvidence",
    "build_manifest",
    "sha256_file",
    "validate_manifest",
    "write_manifest_atomic",
]
