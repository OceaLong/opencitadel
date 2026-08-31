"""Ownership-safe orchestration for deterministic full-stack acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Protocol, TextIO
from urllib.parse import urlparse

from scripts.acceptance.manifest import (
    PLAYWRIGHT_PROJECT_NAMES,
    PRODUCTION_IMAGE_NAMES,
    SERVICE_NAMES,
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
    validate_manifest,
    write_manifest_atomic,
)

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]{2,47}$")
_BEARER = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+")
_PRODUCTION_IMAGES = {
    "api": "opencitadel-api",
    "execution-kernel": "opencitadel-execution-kernel",
    "migrate": "opencitadel-migrate",
    "ui": "opencitadel-ui",
    "sandbox": "opencitadel-sandbox",
    "ops-collector": "opencitadel-ops-collector",
    "ops-actuator": "opencitadel-ops-actuator",
}
_BUILD_SERVICES = (
    "opencitadel-sandbox",
    "opencitadel-migrate",
    "opencitadel-api",
    "opencitadel-execution-kernel",
    "opencitadel-ui",
    "opencitadel-ops-collector",
    "opencitadel-ops-actuator",
    "acceptance-inference",
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> CommandResult: ...


class SandboxEventRecorder(Protocol):
    def stop(self) -> SandboxEvidence: ...


class SubprocessCommandRunner:
    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            env=None if env is None else dict(env),
            timeout=timeout,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
        )
        return CommandResult(tuple(args), completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True, slots=True)
class AcceptanceConfig:
    project_name: str
    run_id: str
    disposable: bool
    evidence_dir: Path
    base_url: str
    ops_console_url: str

    def __post_init__(self) -> None:
        validate_identifier(self.project_name)
        validate_identifier(self.run_id)
        if not self.evidence_dir.is_absolute():
            raise ValueError("evidence_dir must be absolute")
        for name, value in (("base_url", self.base_url), ("ops_console_url", self.ops_console_url)):
            parsed = urlparse(value)
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
                raise ValueError(f"{name} must use loopback HTTP")


def sandbox_event_command(config: AcceptanceConfig) -> tuple[str, ...]:
    """Return an exact-label, read-only Docker event subscription."""

    return (
        "docker",
        "events",
        "--filter",
        "type=container",
        "--filter",
        "label=opencitadel.io/sandbox=true",
        "--filter",
        f"label=com.opencitadel.acceptance.project={config.project_name}",
        "--filter",
        f"label=com.opencitadel.acceptance.run={config.run_id}",
        "--format",
        "{{json .}}",
    )


def summarize_sandbox_events(lines: Sequence[str]) -> SandboxEvidence:
    created: set[str] = set()
    destroyed: set[str] = set()
    for line in lines:
        if not line.strip():
            continue
        try:
            document = json.loads(line)
            action = document["Action"]
            container_id = document["Actor"]["ID"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("sandbox lifecycle event is malformed") from exc
        if not isinstance(action, str) or not isinstance(container_id, str) or not container_id:
            raise ValueError("sandbox lifecycle event is malformed")
        if action == "create":
            created.add(container_id)
        elif action == "destroy":
            destroyed.add(container_id)
    return SandboxEvidence(
        created=len(created),
        drained=len(created.intersection(destroyed)),
    )


@dataclass(slots=True)
class DockerSandboxEventRecorder:
    process: subprocess.Popen
    output: TextIO
    errors: TextIO
    output_path: Path
    error_path: Path
    _evidence: SandboxEvidence | None = None

    @classmethod
    def start(
        cls,
        config: AcceptanceConfig,
        repository_root: Path,
    ) -> DockerSandboxEventRecorder:
        output_path = config.evidence_dir / "sandbox-events.ndjson"
        error_path = config.evidence_dir / "sandbox-events.stderr.log"
        output = output_path.open("w", encoding="utf-8")
        errors = error_path.open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(
                sandbox_event_command(config),
                cwd=repository_root,
                stdout=output,
                stderr=errors,
                text=True,
            )
        except BaseException:
            output.close()
            errors.close()
            raise
        return cls(process, output, errors, output_path, error_path)

    def stop(self) -> SandboxEvidence:
        if self._evidence is not None:
            return self._evidence
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self.output.close()
        self.errors.close()
        if self.process.returncode not in {0, -signal.SIGTERM, 128 + signal.SIGTERM}:
            detail = self.error_path.read_text(encoding="utf-8").strip()
            raise RunnerFailure("sandbox evidence", detail or "Docker event monitor failed")
        self._evidence = summarize_sandbox_events(
            self.output_path.read_text(encoding="utf-8").splitlines()
        )
        return self._evidence


@dataclass(frozen=True, slots=True)
class OwnedResources:
    containers: tuple[str, ...] = ()
    networks: tuple[str, ...] = ()
    volumes: tuple[str, ...] = ()
    dynamic_sandboxes: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not any((self.containers, self.networks, self.volumes, self.dynamic_sandboxes))


class OwnershipError(RuntimeError):
    """A Docker resource matched a query but not the complete ownership identity."""


class RunnerFailure(RuntimeError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"{stage}: {message}")
        self.stage = stage


def validate_identifier(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("identifier must match ^[a-z0-9][a-z0-9-]{2,47}$")
    return value


def compose_network_name(project_name: str, logical_name: str) -> str:
    validate_identifier(project_name)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", logical_name):
        raise ValueError("logical network name is invalid")
    return f"{project_name}_{logical_name}"


def assert_ports_available(ports: Collection[int]) -> None:
    for port in ports:
        if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65_535:
            raise ValueError(f"invalid loopback port: {port}")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"loopback port {port} is already occupied") from exc


def redact(text: str, secrets: Collection[str]) -> str:
    redacted = text
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        redacted = redacted.replace(secret, "[REDACTED]")
    return _BEARER.sub(r"\1[REDACTED]", redacted)


def _digest_frame(digest: hashlib._Hash, kind: str, payload: bytes) -> None:
    kind_bytes = kind.encode("ascii")
    digest.update(len(kind_bytes).to_bytes(2, "big"))
    digest.update(kind_bytes)
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)


def _git_output(
    commands: CommandRunner,
    repository_root: Path,
    args: Sequence[str],
    stage: str,
) -> str:
    result = commands.run(args, cwd=repository_root)
    if result.returncode != 0:
        raise RunnerFailure(stage, result.stderr.strip() or "command failed")
    return result.stdout


def _untracked_file_record(repository_root: Path, relative: str) -> tuple[bytes, bytes, bytes]:
    relative_path = Path(relative)
    if not relative or relative_path.is_absolute() or ".." in relative_path.parts:
        raise RunnerFailure("capture git evidence", f"unsafe untracked path: {relative!r}")
    path = repository_root / relative_path
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode):
        mode = b"120000"
        payload = os.fsencode(os.readlink(path))
    elif stat.S_ISREG(before.st_mode):
        mode = b"100755" if before.st_mode & stat.S_IXUSR else b"100644"
        payload = path.read_bytes()
    else:
        raise RunnerFailure(
            "capture git evidence",
            f"unsupported untracked file type: {relative}",
        )
    after = path.lstat()
    identity = (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns)
    if identity != (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns):
        raise RunnerFailure(
            "capture git evidence",
            f"untracked file changed while hashing: {relative}",
        )
    return relative.encode("utf-8", "surrogateescape"), mode, payload


def capture_git_evidence(commands: CommandRunner, repository_root: Path) -> GitEvidence:
    """Bind one manifest to the exact tracked, staged, and untracked source tree."""

    revision = _git_output(
        commands,
        repository_root,
        ["git", "rev-parse", "HEAD"],
        "capture Git revision",
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{7,64}", revision):
        revision = "0" * 40

    digest = hashlib.sha256()
    _digest_frame(digest, "schema", b"opencitadel-dirty-tree-v2")
    for kind, args in (
        ("status", ["git", "status", "--porcelain=v2", "-z"]),
        ("tracked-diff", ["git", "diff", "--binary"]),
        ("staged-diff", ["git", "diff", "--cached", "--binary"]),
    ):
        output = _git_output(commands, repository_root, args, f"capture Git {kind}")
        _digest_frame(digest, kind, output.encode("utf-8", "surrogateescape"))

    untracked_output = _git_output(
        commands,
        repository_root,
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        "capture Git untracked paths",
    )
    paths = sorted(item for item in untracked_output.split("\0") if item)
    _digest_frame(digest, "untracked-count", str(len(paths)).encode("ascii"))
    for relative in paths:
        path_bytes, mode, payload = _untracked_file_record(repository_root, relative)
        _digest_frame(digest, "untracked-path", path_bytes)
        _digest_frame(digest, "untracked-mode", mode)
        _digest_frame(digest, "untracked-content", payload)
    return GitEvidence(revision=revision, dirty_tree_digest=digest.hexdigest())


def _lines(result: CommandResult, stage: str) -> tuple[str, ...]:
    if result.returncode != 0:
        raise RunnerFailure(stage, result.stderr.strip() or "command failed")
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def _docker_query(
    commands: CommandRunner,
    repository_root: Path,
    args: Sequence[str],
    stage: str,
) -> tuple[str, ...]:
    return _lines(commands.run(args, cwd=repository_root), stage)


def _dynamic_sandbox_identity(
    commands: CommandRunner,
    config: AcceptanceConfig,
    repository_root: Path,
    container_id: str,
) -> None:
    result = commands.run(
        ["docker", "inspect", "--format", "{{json .}}", container_id],
        cwd=repository_root,
    )
    lines = _lines(result, "inspect dynamic sandbox")
    if len(lines) != 1:
        raise OwnershipError(f"dynamic sandbox {container_id} does not have one inspect record")
    try:
        document = json.loads(lines[0])
        name = str(document["Name"]).lstrip("/")
        labels = document["Config"]["Labels"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise OwnershipError(f"dynamic sandbox {container_id} has malformed identity") from exc
    expected = {
        "opencitadel.io/sandbox": "true",
        "com.docker.compose.project": config.project_name,
        "com.opencitadel.acceptance.project": config.project_name,
        "com.opencitadel.acceptance.run": config.run_id,
    }
    prefix = f"{config.project_name}-sandbox-"
    if not name.startswith(prefix) or any(
        labels.get(key) != value for key, value in expected.items()
    ):
        raise OwnershipError(
            f"dynamic sandbox {container_id} does not match the acceptance identity"
        )


def owned_resources(
    commands: CommandRunner,
    config: AcceptanceConfig,
    repository_root: Path,
) -> OwnedResources:
    label_filters = [
        "--filter",
        f"label=com.docker.compose.project={config.project_name}",
        "--filter",
        f"label=com.opencitadel.acceptance.run={config.run_id}",
    ]
    containers = _docker_query(
        commands,
        repository_root,
        ["docker", "ps", "-aq", *label_filters],
        "query owned containers",
    )
    networks = _docker_query(
        commands,
        repository_root,
        ["docker", "network", "ls", "-q", *label_filters],
        "query owned networks",
    )
    volumes = _docker_query(
        commands,
        repository_root,
        ["docker", "volume", "ls", "-q", *label_filters],
        "query owned volumes",
    )
    dynamic = _docker_query(
        commands,
        repository_root,
        [
            "docker",
            "ps",
            "-aq",
            *label_filters,
            "--filter",
            "label=opencitadel.io/sandbox=true",
            "--filter",
            f"name={config.project_name}-sandbox-",
        ],
        "query dynamic sandboxes",
    )
    for container_id in dynamic:
        _dynamic_sandbox_identity(commands, config, repository_root, container_id)
    dynamic_ids = frozenset(dynamic)
    return OwnedResources(
        containers=tuple(item for item in containers if item not in dynamic_ids),
        networks=networks,
        volumes=volumes,
        dynamic_sandboxes=dynamic,
    )


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value
    return values


def _url_port(url: str) -> int:
    parsed = urlparse(url)
    if parsed.port is None:
        raise ValueError(f"URL requires an explicit port: {url}")
    return parsed.port


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class AcceptanceRunner:
    def __init__(
        self,
        config: AcceptanceConfig,
        *,
        commands: CommandRunner,
        repository_root: Path,
        readiness_probe: Callable[[str], bool],
        playwright_projects: Sequence[str] = (),
        readiness_timeout_seconds: int = 300,
        shutdown_timeout_seconds: int = 45,
        fault: str = "none",
        sandbox_event_recorder_factory: (
            Callable[[AcceptanceConfig, Path], SandboxEventRecorder] | None
        ) = None,
    ) -> None:
        if fault not in {"none", "playwright", "readiness"}:
            raise ValueError("unknown acceptance fault")
        if readiness_timeout_seconds <= 0 or shutdown_timeout_seconds <= 0:
            raise ValueError("runner timeouts must be positive")
        unknown_projects = set(playwright_projects) - PLAYWRIGHT_PROJECT_NAMES
        if unknown_projects:
            raise ValueError(f"unknown Playwright projects: {sorted(unknown_projects)}")
        self.config = config
        self.commands = commands
        self.repository_root = repository_root.resolve()
        self.readiness_probe = readiness_probe
        self.playwright_projects = tuple(playwright_projects)
        self.readiness_timeout_seconds = readiness_timeout_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self.fault = fault
        self._sandbox_event_recorder_factory = sandbox_event_recorder_factory
        self._cancel_requested = False
        self._started = False
        self._environment = self._build_environment()
        self._secrets = {
            value
            for key, value in self._environment.items()
            if value
            and any(
                marker in key for marker in ("SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "KEY")
            )
        }

    def _build_environment(self) -> dict[str, str]:
        environment = {**os.environ, **_load_dotenv(self.repository_root / ".env.e2e")}
        nginx_port = _url_port(self.config.base_url)
        ops_port = _url_port(self.config.ops_console_url)
        labels = {
            "com.docker.compose.project": self.config.project_name,
            "com.opencitadel.acceptance.project": self.config.project_name,
            "com.opencitadel.acceptance.run": self.config.run_id,
        }
        environment.update(
            {
                "COMPOSE_PROJECT_NAME": self.config.project_name,
                "ACCEPTANCE_PROJECT_ID": self.config.project_name,
                "ACCEPTANCE_RUN_ID": self.config.run_id,
                "SANDBOX_NAME_PREFIX": f"{self.config.project_name}-sandbox",
                "SANDBOX_NETWORK": compose_network_name(
                    self.config.project_name, "opencitadel-sandbox-network"
                ),
                "SANDBOX_LABELS": json.dumps(labels, sort_keys=True, separators=(",", ":")),
                "NGINX_PORT": str(nginx_port),
                "NGINX_HTTPS_PORT": str(nginx_port + 1),
                "OPS_CONSOLE_PORT": str(ops_port),
                "OPS_CONSOLE_URL": self.config.ops_console_url,
                "FRONTEND_BASE_URL": self.config.base_url,
                "OAUTH_REDIRECT_BASE": f"{self.config.base_url}/api/auth/oauth",
                "PLAYWRIGHT_BASE_URL": self.config.base_url,
                "ACCEPTANCE_EVIDENCE_DIR": str(self.config.evidence_dir),
                "ACCEPTANCE_PLAYWRIGHT_PROJECTS": ",".join(
                    self.playwright_projects or sorted(PLAYWRIGHT_PROJECT_NAMES)
                ),
            }
        )
        return environment

    @property
    def _compose(self) -> list[str]:
        return [
            "docker",
            "compose",
            "--project-name",
            self.config.project_name,
            "--env-file",
            ".env.e2e",
            "--profile",
            "local",
            "--profile",
            "demo",
            "--profile",
            "patrol",
            "--profile",
            "acceptance",
        ]

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _raise_if_cancelled(self) -> None:
        if self._cancel_requested:
            raise RunnerFailure("cancelled", "termination requested")

    def _write_lifecycle(
        self,
        state: str,
        *,
        result_status: str | None = None,
    ) -> None:
        write_manifest_atomic(
            self.config.evidence_dir / "lifecycle.json",
            {
                "schema_version": 1,
                "project_name": self.config.project_name,
                "run_id": self.config.run_id,
                "runner_pid": os.getpid(),
                "state": state,
                "result_status": result_status,
                "updated_at": _utc_now(),
            },
        )

    def _run(self, args: Sequence[str], stage: str, *, cwd: Path | None = None) -> CommandResult:
        result = self.commands.run(
            args,
            cwd=cwd or self.repository_root,
            env=self._environment,
        )
        if result.returncode != 0:
            streams = [
                stream.strip()[-12_000:]
                for stream in (result.stdout, result.stderr)
                if stream.strip()
            ]
            detail = redact("\n".join(streams) or "command failed", self._secrets)
            raise RunnerFailure(stage, detail)
        return result

    def _preflight(self) -> None:
        if self.config.evidence_dir.exists():
            raise RunnerFailure("preflight", "evidence directory already exists")
        assert_ports_available(
            (
                _url_port(self.config.base_url),
                _url_port(self.config.base_url) + 1,
                _url_port(self.config.ops_console_url),
            )
        )
        existing = owned_resources(self.commands, self.config, self.repository_root)
        if not existing.empty:
            raise RunnerFailure(
                "preflight", "the exact project and run identity already owns resources"
            )

    def _wait_ready(self) -> None:
        if self.fault == "readiness":
            raise RunnerFailure("readiness", "fault injection")
        for url in (
            f"{self.config.base_url}/api/health/ready",
            f"{self.config.ops_console_url}/health",
        ):
            deadline = time.monotonic() + self.readiness_timeout_seconds
            while not self.readiness_probe(url):
                if self._cancel_requested:
                    raise RunnerFailure("cancelled", "termination requested")
                if time.monotonic() >= deadline:
                    raise RunnerFailure("readiness", f"timed out waiting for {url}")
                time.sleep(0.25)

    def _run_playwright(self) -> None:
        self._raise_if_cancelled()
        self._run(["npm", "ci"], "install Playwright", cwd=self.repository_root / "e2e")
        self._raise_if_cancelled()
        if self.fault == "playwright":
            raise RunnerFailure("playwright", "fault injection")
        args = ["npx", "playwright", "test"]
        for project in self.playwright_projects:
            args.extend(("--project", project))
        self._run(args, "playwright", cwd=self.repository_root / "e2e")
        self._raise_if_cancelled()

    def _capture_logs(self) -> None:
        result = self.commands.run(
            [*self._compose, "logs", "--no-color", "--timestamps"],
            cwd=self.repository_root,
            env=self._environment,
        )
        logs = f"{result.stdout}\n{result.stderr}".strip() + "\n"
        path = self.config.evidence_dir / "logs/stack.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(redact(logs, self._secrets), encoding="utf-8")

    def _capture_images(self) -> ImageEvidence:
        production: dict[str, str] = {}
        for name, image in sorted(_PRODUCTION_IMAGES.items()):
            result = self.commands.run(
                ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                cwd=self.repository_root,
            )
            lines = _lines(result, f"inspect image {image}")
            production[name] = lines[0]
        provider_result = self.commands.run(
            [
                "docker",
                "image",
                "inspect",
                "opencitadel-acceptance-inference",
                "--format",
                "{{.Id}}",
            ],
            cwd=self.repository_root,
        )
        return ImageEvidence(
            production=production,
            acceptance_provider=_lines(provider_result, "inspect acceptance provider")[0],
        )

    def _capture_services(self, ready_at: str) -> tuple[ServiceEvidence, ...]:
        result = self.commands.run(
            [*self._compose, "ps", "--all", "--format", "json"],
            cwd=self.repository_root,
            env=self._environment,
        )
        if result.returncode != 0:
            return ()
        try:
            parsed = json.loads(result.stdout or "[]")
            records = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        by_service = {
            record.get("Service"): record for record in records if isinstance(record, Mapping)
        }
        evidence = []
        for name in sorted(SERVICE_NAMES):
            record = by_service.get(name)
            if record is None:
                evidence.append(ServiceEvidence(name, "not_started", 0, None))
                continue
            health = str(record.get("Health") or "").lower()
            state = str(record.get("State") or "").lower()
            exit_code = record.get("ExitCode")
            if name == "opencitadel-migrate" and state == "exited" and exit_code == 0:
                normalized_health = "completed"
            elif health == "healthy":
                normalized_health = "healthy"
            elif health in {"starting", "unhealthy"}:
                normalized_health = health
            else:
                normalized_health = "not_started"
            restart_count = record.get("RestartCount", 0)
            if not isinstance(restart_count, int) or isinstance(restart_count, bool):
                restart_count = 0
            evidence.append(
                ServiceEvidence(
                    name=name,
                    health=normalized_health,
                    restart_count=restart_count,
                    ready_at=ready_at if normalized_health in {"healthy", "completed"} else None,
                )
            )
        return tuple(evidence)

    def _capture_migration(self) -> MigrationEvidence:
        result = self.commands.run(
            [*self._compose, "exec", "-T", "opencitadel-api", "python", "-m", "alembic", "heads"],
            cwd=self.repository_root,
            env=self._environment,
        )
        head = (
            result.stdout.strip().split(maxsplit=1)[0] if result.returncode == 0 else "unavailable"
        )
        return MigrationEvidence(alembic_head=head or "unavailable")

    def _capture_sandbox_lifecycle(
        self,
        *,
        since: str,
        until: str,
    ) -> SandboxEvidence:
        filters = [
            "--filter",
            "type=container",
            "--filter",
            "label=opencitadel.io/sandbox=true",
            "--filter",
            f"label=com.opencitadel.acceptance.project={self.config.project_name}",
            "--filter",
            f"label=com.opencitadel.acceptance.run={self.config.run_id}",
        ]

        def event_ids(event: str) -> frozenset[str]:
            return frozenset(
                _docker_query(
                    self.commands,
                    self.repository_root,
                    [
                        "docker",
                        "events",
                        "--since",
                        since,
                        "--until",
                        until,
                        *filters,
                        "--filter",
                        f"event={event}",
                        "--format",
                        "{{.Actor.ID}}",
                    ],
                    f"query dynamic sandbox {event} events",
                )
            )

        created = event_ids("create")
        destroyed = event_ids("destroy")
        return SandboxEvidence(
            created=len(created),
            drained=len(created.intersection(destroyed)),
        )

    def _capture_git(self) -> GitEvidence:
        return capture_git_evidence(self.commands, self.repository_root)

    def _read_playwright(
        self,
    ) -> tuple[tuple[PlaywrightProjectEvidence, ...], tuple[CoverageEvidence, ...]]:
        path = self.config.evidence_dir / "playwright/results.json"
        if not path.is_file():
            return (), ()
        document = json.loads(path.read_text(encoding="utf-8"))
        projects = tuple(PlaywrightProjectEvidence(**item) for item in document.get("projects", []))
        coverage = tuple(CoverageEvidence(**item) for item in document.get("coverage", []))
        return projects, coverage

    def _cleanup(self) -> tuple[OwnedResources, list[str]]:
        errors = []
        stop = self.commands.run(
            [*self._compose, "stop", "--timeout", str(self.shutdown_timeout_seconds)],
            cwd=self.repository_root,
            env=self._environment,
        )
        if stop.returncode != 0:
            errors.append(f"stop: {stop.stderr.strip() or 'command failed'}")

        def inspect_owned() -> OwnedResources | None:
            try:
                return owned_resources(self.commands, self.config, self.repository_root)
            except (OwnershipError, RunnerFailure) as exc:
                errors.append(str(exc))
                return None

        def remove_dynamic(resources: OwnedResources) -> None:
            for container_id in resources.dynamic_sandboxes:
                result = self.commands.run(
                    ["docker", "rm", "-f", container_id], cwd=self.repository_root
                )
                if result.returncode != 0:
                    errors.append(f"remove dynamic sandbox {container_id}: {result.stderr.strip()}")

        # Quiesce every sandbox producer before taking the first ownership
        # snapshot. A sandbox created while compose services are stopping is
        # then visible here and cannot be replaced by a new one.
        before = inspect_owned()
        if before is not None:
            remove_dynamic(before)

        down_args = [*self._compose, "down", "--remove-orphans"]
        if self.config.disposable:
            down_args.append("--volumes")

        def compose_down() -> None:
            down = self.commands.run(down_args, cwd=self.repository_root, env=self._environment)
            if down.returncode != 0:
                errors.append(f"down: {down.stderr.strip() or 'command failed'}")

        compose_down()
        residue = OwnedResources()
        for attempt in range(3):
            inspected = inspect_owned()
            if inspected is None:
                break
            residue = inspected
            runtime_residue = bool(
                residue.containers or residue.networks or residue.dynamic_sandboxes
            )
            volume_residue = self.config.disposable and bool(residue.volumes)
            if not runtime_residue and not volume_residue:
                break
            if attempt == 2:
                break
            remove_dynamic(residue)
            compose_down()
        if residue.containers or residue.networks or residue.dynamic_sandboxes:
            errors.append("owned runtime residue remains after cleanup")
        if self.config.disposable and residue.volumes:
            errors.append("owned volume residue remains after disposable cleanup")
        return residue, errors

    def _write_manifest(
        self,
        *,
        started_at: str,
        started_monotonic: float,
        failure_reason: str | None,
        images: ImageEvidence,
        git: GitEvidence,
        migration: MigrationEvidence,
        services: tuple[ServiceEvidence, ...],
        residue: OwnedResources,
        sandboxes: SandboxEvidence,
    ) -> list[str]:
        projects, coverage = self._read_playwright()
        artifacts = tuple(
            ArtifactInput(kind=kind, path=path)
            for kind, path in (
                ("junit", self.config.evidence_dir / "playwright/junit.xml"),
                ("playwright-json", self.config.evidence_dir / "playwright/results.json"),
                ("logs", self.config.evidence_dir / "logs/stack.log"),
            )
            if path.is_file()
        )
        status = "failed" if failure_reason else "passed"
        document = build_manifest(
            ManifestInputs(
                evidence_root=self.config.evidence_dir,
                scope=ScopeEvidence(
                    kind=(
                        "full"
                        if not self.playwright_projects
                        or frozenset(self.playwright_projects) == PLAYWRIGHT_PROJECT_NAMES
                        else "partial"
                    ),
                    requested_projects=tuple(
                        self.playwright_projects or sorted(PLAYWRIGHT_PROJECT_NAMES)
                    ),
                ),
                result=ResultEvidence(
                    status=status,
                    failure_reason=failure_reason,
                    started_at=started_at,
                    finished_at=_utc_now(),
                    duration_ms=max(0, int((time.monotonic() - started_monotonic) * 1000)),
                ),
                git=git,
                compose=ComposeEvidence(
                    project_name=self.config.project_name,
                    run_id=self.config.run_id,
                    disposable=self.config.disposable,
                ),
                images=images,
                migration=migration,
                services=services,
                playwright_projects=projects,
                coverage=coverage,
                sandboxes=sandboxes,
                residue=ResidueEvidence(
                    containers=len(residue.containers),
                    networks=len(residue.networks),
                    dynamic_sandboxes=len(residue.dynamic_sandboxes),
                    volumes=len(residue.volumes),
                    retained_volumes=residue.volumes if not self.config.disposable else (),
                ),
                artifacts=artifacts,
            )
        )
        write_manifest_atomic(self.config.evidence_dir / "manifest.json", document)
        return validate_manifest(document, self.config.evidence_dir)

    def execute(self) -> int:
        started_at = _utc_now()
        started_monotonic = time.monotonic()
        failure_reason: str | None = None
        images = ImageEvidence(
            production=dict.fromkeys(PRODUCTION_IMAGE_NAMES, f"sha256:{'0' * 64}"),
            acceptance_provider=f"sha256:{'0' * 64}",
        )
        migration = MigrationEvidence(alembic_head="unavailable")
        services: tuple[ServiceEvidence, ...] = ()
        residue = OwnedResources()
        sandboxes = SandboxEvidence(created=0, drained=0)
        git_evidence = GitEvidence(revision="0" * 40, dirty_tree_digest="0" * 64)
        sandbox_event_recorder: SandboxEventRecorder | None = None
        evidence_created = False
        try:
            self._preflight()
            git_evidence = self._capture_git()
            self.config.evidence_dir.mkdir(parents=True, exist_ok=False)
            evidence_created = True
            self._write_lifecycle("evidence_ready")
            if self._sandbox_event_recorder_factory is not None:
                sandbox_event_recorder = self._sandbox_event_recorder_factory(
                    self.config,
                    self.repository_root,
                )
            self._run(
                [*self._compose, "build", *_BUILD_SERVICES],
                "build images",
            )
            self._write_lifecycle("images_built")
            images = self._capture_images()
            # Compose may create containers, networks, and volumes before
            # `up --wait` reports an unhealthy service. Cleanup ownership
            # therefore begins with the attempt, not with a successful return.
            self._started = True
            self._run([*self._compose, "up", "-d", "--build", "--wait"], "start stack")
            self._write_lifecycle("stack_started")
            self._raise_if_cancelled()
            self._wait_ready()
            self._write_lifecycle("stack_ready")
            self._raise_if_cancelled()
            self._run_playwright()
        except (OSError, RuntimeError, ValueError) as exc:
            failure_reason = str(exc)
        finally:
            if evidence_created:
                self._write_lifecycle("cleanup")
            if self._started:
                diagnostic_errors: list[str] = []
                try:
                    services = self._capture_services(_utc_now())
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    diagnostic_errors.append(f"services: {exc}")
                try:
                    migration = self._capture_migration()
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    diagnostic_errors.append(f"migration: {exc}")
                try:
                    self._capture_logs()
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    diagnostic_errors.append(f"logs: {exc}")
                try:
                    residue, cleanup_errors = self._cleanup()
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    cleanup_errors = [f"unhandled cleanup failure: {exc}"]
                cleanup_errors = [*diagnostic_errors, *cleanup_errors]
                if cleanup_errors:
                    cleanup_reason = f"cleanup: {'; '.join(cleanup_errors)}"
                    failure_reason = (
                        f"{failure_reason}; {cleanup_reason}" if failure_reason else cleanup_reason
                    )
            try:
                if sandbox_event_recorder is not None:
                    sandboxes = sandbox_event_recorder.stop()
                elif self._started:
                    sandboxes = self._capture_sandbox_lifecycle(
                        since=started_at,
                        until=_utc_now(),
                    )
            except (OSError, OwnershipError, RunnerFailure, ValueError) as exc:
                evidence_reason = f"sandbox evidence: {exc}"
                failure_reason = (
                    f"{failure_reason}; {evidence_reason}" if failure_reason else evidence_reason
                )
            if evidence_created:
                try:
                    final_git_evidence = self._capture_git()
                    if final_git_evidence != git_evidence:
                        source_reason = "source tree changed during acceptance"
                        failure_reason = (
                            f"{failure_reason}; {source_reason}"
                            if failure_reason
                            else source_reason
                        )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    source_reason = f"source evidence: {exc}"
                    failure_reason = (
                        f"{failure_reason}; {source_reason}" if failure_reason else source_reason
                    )
                validation_errors = self._write_manifest(
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    failure_reason=failure_reason,
                    images=images,
                    git=git_evidence,
                    migration=migration,
                    services=services,
                    residue=residue,
                    sandboxes=sandboxes,
                )
                if validation_errors and failure_reason is None:
                    failure_reason = f"manifest: {'; '.join(validation_errors)}"
                    self._write_manifest(
                        started_at=started_at,
                        started_monotonic=started_monotonic,
                        failure_reason=failure_reason,
                        images=images,
                        git=git_evidence,
                        migration=migration,
                        services=services,
                        residue=residue,
                        sandboxes=sandboxes,
                    )
                self._write_lifecycle(
                    "complete",
                    result_status="failed" if failure_reason else "passed",
                )
        if failure_reason:
            print(
                f"acceptance failed: {redact(failure_reason, self._secrets)}",
                file=sys.stderr,
                flush=True,
            )
        return 1 if failure_reason else 0


def _probe(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def _derived_ports(run_id: str) -> tuple[int, int, int]:
    bucket = int(hashlib.sha256(run_id.encode()).hexdigest()[:8], 16) % 10_000
    nginx = 20_000 + bucket
    return nginx, nginx + 1, 40_000 + bucket


def _default_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return validate_identifier(f"run-{timestamp}-{os.getpid()}")


def _resolve_evidence_dir(repository_root: Path, evidence_root: str, run_id: str) -> Path:
    relative = Path(evidence_root)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("evidence root must be repository-relative")
    resolved = (repository_root / relative / run_id).resolve()
    allowed = (repository_root / "tmp/acceptance").resolve()
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise ValueError("evidence root must be under tmp/acceptance") from exc
    return resolved


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-project-name")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--playwright-project",
        action="append",
        choices=sorted(PLAYWRIGHT_PROJECT_NAMES),
        default=[],
    )
    parser.add_argument("--disposable", action="store_true")
    parser.add_argument("--readiness-timeout-seconds", type=int, default=300)
    parser.add_argument("--shutdown-timeout-seconds", type=int, default=45)
    parser.add_argument("--evidence-root", default="tmp/acceptance")
    parser.add_argument("--fault", choices=("none", "playwright", "readiness"), default="none")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository_root = Path(__file__).resolve().parents[2]
    for executable in ("docker", "npm", "npx"):
        if shutil.which(executable) is None:
            print(f"missing required command: {executable}", file=sys.stderr)
            return 2
    run_id = validate_identifier(args.run_id or _default_run_id())
    project_name = validate_identifier(
        args.compose_project_name or f"opencitadel-acceptance-{run_id}"[:48].rstrip("-")
    )
    if args.fault != "none" and os.environ.get("ACCEPTANCE_ALLOW_FAULT_INJECTION") != "1":
        print("--fault requires ACCEPTANCE_ALLOW_FAULT_INJECTION=1", file=sys.stderr)
        return 2
    nginx_port, _, ops_port = _derived_ports(run_id)
    config = AcceptanceConfig(
        project_name=project_name,
        run_id=run_id,
        disposable=args.disposable,
        evidence_dir=_resolve_evidence_dir(repository_root, args.evidence_root, run_id),
        base_url=f"http://127.0.0.1:{nginx_port}",
        ops_console_url=f"http://127.0.0.1:{ops_port}",
    )
    runner = AcceptanceRunner(
        config,
        commands=SubprocessCommandRunner(),
        repository_root=repository_root,
        readiness_probe=_probe,
        playwright_projects=args.playwright_project,
        readiness_timeout_seconds=args.readiness_timeout_seconds,
        shutdown_timeout_seconds=args.shutdown_timeout_seconds,
        fault=args.fault,
        sandbox_event_recorder_factory=DockerSandboxEventRecorder.start,
    )

    def request_cancel(_signum: int, _frame: FrameType | None) -> None:
        runner.request_cancel()

    signal.signal(signal.SIGINT, request_cancel)
    signal.signal(signal.SIGTERM, request_cancel)
    return runner.execute()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AcceptanceConfig",
    "AcceptanceRunner",
    "CommandResult",
    "DockerSandboxEventRecorder",
    "OwnedResources",
    "OwnershipError",
    "assert_ports_available",
    "capture_git_evidence",
    "compose_network_name",
    "main",
    "owned_resources",
    "redact",
    "sandbox_event_command",
    "summarize_sandbox_events",
    "validate_identifier",
]
