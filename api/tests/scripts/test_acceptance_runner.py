from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.acceptance.manifest import (  # noqa: E402
    ACCEPTANCE_PROJECT_REQUIREMENTS,
    PLAYWRIGHT_PROJECT_NAMES,
    SERVICE_NAMES,
    GitEvidence,
    SandboxEvidence,
    validate_manifest,
)
from scripts.acceptance.runner import (  # noqa: E402
    AcceptanceConfig,
    AcceptanceRunner,
    CommandResult,
    OwnershipError,
    assert_ports_available,
    compose_network_name,
    owned_resources,
    redact,
    sandbox_event_command,
    summarize_sandbox_events,
    validate_identifier,
)


class FakeCommandRunner:
    def __init__(
        self,
        evidence_dir: Path,
        *,
        collision: bool = False,
        playwright_exit: int = 0,
        playwright_stdout: str = "",
        playwright_stderr: str = "playwright failed",
        cleanup_failure: bool = False,
        retain_volumes: bool = False,
        mismatched_dynamic_identity: bool = False,
        sandbox_drained_before_cleanup: bool = False,
        late_sandbox_after_first_down: bool = False,
        up_failure: bool = False,
    ) -> None:
        self.evidence_dir = evidence_dir
        self.collision = collision
        self.playwright_exit = playwright_exit
        self.playwright_stdout = playwright_stdout
        self.playwright_stderr = playwright_stderr
        self.cleanup_failure = cleanup_failure
        self.retain_volumes = retain_volumes
        self.mismatched_dynamic_identity = mismatched_dynamic_identity
        self.sandbox_drained_before_cleanup = sandbox_drained_before_cleanup
        self.late_sandbox_after_first_down = late_sandbox_after_first_down
        self.up_failure = up_failure
        self.late_network_present = False
        self.down_calls = 0
        self.started = False
        self.ever_started = False
        self.dynamic_present = False
        self.calls: list[tuple[str, ...]] = []
        self.on_up = None
        self.on_npm_ci = None

    def _result(self, args, returncode=0, stdout="", stderr="") -> CommandResult:
        return CommandResult(tuple(args), returncode, stdout, stderr)

    def _write_playwright_results(self, environment) -> None:
        output = self.evidence_dir / "playwright/results.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        failed = 1 if self.playwright_exit else 0
        requested = frozenset(environment["ACCEPTANCE_PLAYWRIGHT_PROJECTS"].split(","))
        executed = set(requested)
        if requested - {"cleanup"}:
            executed.update({"bootstrap", "cleanup"})
        requirements = {
            requirement_id: project
            for project in requested
            for requirement_id in ACCEPTANCE_PROJECT_REQUIREMENTS[project]
        }
        output.write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "name": name,
                            "tests": 1 if name in executed else 0,
                            "passed": (
                                0 if name not in executed or (failed and name == "execution") else 1
                            ),
                            "failed": failed if name == "execution" else 0,
                            "skipped": 0,
                            "duration_ms": 10 if name in executed else 0,
                        }
                        for name in sorted(PLAYWRIGHT_PROJECT_NAMES)
                    ],
                    "coverage": [
                        {
                            "requirement_id": requirement_id,
                            "test_id": f"acceptance::{index:02d}",
                            "project": project,
                            "status": "failed"
                            if failed and requirement_id == "RUN-AGENT"
                            else "passed",
                        }
                        for index, (requirement_id, project) in enumerate(
                            sorted(requirements.items())
                        )
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        junit = self.evidence_dir / "playwright/junit.xml"
        junit.write_text("<testsuites/>\n", encoding="utf-8")

    def run(self, args, *, cwd, env=None, timeout=None) -> CommandResult:
        del cwd, timeout
        args = tuple(str(item) for item in args)
        self.calls.append(args)

        if args[:3] == ("docker", "ps", "-aq"):
            if "opencitadel.io/sandbox=true" in " ".join(args):
                return self._result(args, stdout="sandbox-id\n" if self.dynamic_present else "")
            return self._result(args, stdout="collision-id\n" if self.collision else "")
        if args[:3] == ("docker", "network", "ls"):
            network_present = self.collision or self.late_network_present
            return self._result(args, stdout="collision-network\n" if network_present else "")
        if args[:3] == ("docker", "volume", "ls"):
            if self.collision:
                return self._result(args, stdout="collision-volume\n")
            if self.retain_volumes and self.ever_started and not self.started:
                return self._result(args, stdout="retained-postgres\nretained-redis\n")
            return self._result(args)
        if args[:3] == ("docker", "inspect", "--format"):
            labels = {
                "opencitadel.io/sandbox": "true",
                "com.docker.compose.project": "opencitadel-acceptance-run-a",
                "com.opencitadel.acceptance.project": "opencitadel-acceptance-run-a",
                "com.opencitadel.acceptance.run": (
                    "another-run" if self.mismatched_dynamic_identity else "run-a"
                ),
            }
            return self._result(
                args,
                stdout=json.dumps(
                    {
                        "Id": "sandbox-id",
                        "Name": "/opencitadel-acceptance-run-a-sandbox-deadbeef",
                        "Config": {"Labels": labels},
                    }
                )
                + "\n",
            )
        if args[:3] == ("docker", "rm", "-f"):
            self.dynamic_present = False
            return self._result(args, stdout="sandbox-id\n")
        if args[:3] == ("docker", "image", "inspect"):
            seed = args[3].encode()
            import hashlib

            return self._result(args, stdout=f"sha256:{hashlib.sha256(seed).hexdigest()}\n")
        if args[:2] == ("docker", "events"):
            return self._result(args, stdout="sandbox-id\n" if self.ever_started else "")

        if args[:2] == ("npm", "ci"):
            if self.on_npm_ci is not None:
                self.on_npm_ci()
            return self._result(args)
        if args[:2] == ("npx", "playwright"):
            self._write_playwright_results(env)
            if self.sandbox_drained_before_cleanup:
                self.dynamic_present = False
            return self._result(
                args,
                returncode=self.playwright_exit,
                stdout=self.playwright_stdout,
                stderr=self.playwright_stderr,
            )

        if args[:2] == ("docker", "compose"):
            if "build" in args:
                return self._result(args)
            if "up" in args:
                self.started = True
                self.ever_started = True
                self.dynamic_present = True
                if self.on_up is not None:
                    self.on_up()
                return self._result(
                    args,
                    returncode=1 if self.up_failure else 0,
                    stderr="service became unhealthy" if self.up_failure else "",
                )
            if "logs" in args:
                return self._result(
                    args,
                    stdout=(
                        "provider token=acceptance-provider-token\n"
                        "Authorization: Bearer externally-visible-secret\n"
                    ),
                )
            if "ps" in args:
                services = [
                    {
                        "Service": name,
                        "Health": "" if name == "opencitadel-migrate" else "healthy",
                        "State": "exited" if name == "opencitadel-migrate" else "running",
                        "ExitCode": 0,
                        "RestartCount": 0,
                    }
                    for name in sorted(SERVICE_NAMES)
                ]
                return self._result(args, stdout=json.dumps(services))
            if "exec" in args:
                return self._result(args, stdout="202608270001 (head)\n")
            if "stop" in args:
                return self._result(args)
            if "down" in args:
                self.down_calls += 1
                if self.cleanup_failure:
                    return self._result(args, returncode=1, stderr="cleanup failed")
                self.started = False
                self.late_network_present = False
                if self.late_sandbox_after_first_down and self.down_calls == 1:
                    self.dynamic_present = True
                    self.late_network_present = True
                else:
                    self.dynamic_present = False
                return self._result(args)
        return self._result(args)


def _config(tmp_path: Path, *, disposable: bool = True) -> AcceptanceConfig:
    return AcceptanceConfig(
        project_name="opencitadel-acceptance-run-a",
        run_id="run-a",
        disposable=disposable,
        evidence_dir=tmp_path / "evidence",
        base_url="http://127.0.0.1:28088",
        ops_console_url="http://127.0.0.1:29099",
    )


@pytest.mark.parametrize(
    "value",
    ["UPPERCASE", "has_underscore", "-leading", "ab", "a" * 49, "shell;command"],
)
def test_validate_identifier_rejects_unsafe_or_ambiguous_values(value: str) -> None:
    with pytest.raises(ValueError, match="identifier"):
        validate_identifier(value)


def test_validate_identifier_and_network_name_preserve_exact_project_scope() -> None:
    assert validate_identifier("opencitadel-acceptance-a1") == "opencitadel-acceptance-a1"
    assert compose_network_name("opencitadel-acceptance-a1", "opencitadel-sandbox-network") == (
        "opencitadel-acceptance-a1_opencitadel-sandbox-network"
    )


def test_assert_ports_available_rejects_an_occupied_loopback_port() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        with pytest.raises(RuntimeError, match=str(port)):
            assert_ports_available((port,))


def test_runner_exports_the_allocated_ops_console_url_to_playwright(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = AcceptanceRunner(
        config,
        commands=FakeCommandRunner(config.evidence_dir),
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )

    assert runner._environment["OPS_CONSOLE_URL"] == config.ops_console_url


def test_git_evidence_digest_binds_same_path_untracked_file_bytes(tmp_path: Path) -> None:
    class GitStateCommands:
        def run(self, args, *, cwd, env=None, timeout=None) -> CommandResult:
            del cwd, env, timeout
            command = tuple(args)
            if command == ("git", "rev-parse", "HEAD"):
                return CommandResult(command, 0, f"{'a' * 40}\n", "")
            if command == ("git", "ls-files", "--others", "--exclude-standard", "-z"):
                return CommandResult(command, 0, "new-source.py\0", "")
            if command[:2] == ("git", "status"):
                return CommandResult(command, 0, "? new-source.py\0", "")
            if command[:2] == ("git", "diff"):
                return CommandResult(command, 0, "", "")
            raise AssertionError(f"unexpected command: {command}")

    source = tmp_path / "new-source.py"
    source.write_text("VALUE = 'first'\n", encoding="utf-8")
    runner = object.__new__(AcceptanceRunner)
    runner.commands = GitStateCommands()
    runner.repository_root = tmp_path.resolve()

    first = runner._capture_git().dirty_tree_digest
    source.write_text("VALUE = 'other'\n", encoding="utf-8")
    second = runner._capture_git().dirty_tree_digest

    assert second != first


def test_runner_fails_evidence_when_source_tree_changes_during_the_run(tmp_path: Path) -> None:
    config = _config(tmp_path)
    commands = FakeCommandRunner(config.evidence_dir)
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )
    snapshots = iter(
        (
            GitEvidence(revision="a" * 40, dirty_tree_digest="1" * 64),
            GitEvidence(revision="a" * 40, dirty_tree_digest="2" * 64),
        )
    )
    runner._capture_git = lambda: next(snapshots)

    assert runner.execute() == 1
    manifest = json.loads((config.evidence_dir / "manifest.json").read_text())

    assert "source tree changed during acceptance" in manifest["result"]["failure_reason"]
    assert manifest["git"]["dirty_tree_digest"] == "1" * 64


def test_redact_removes_exact_secrets_and_bearer_headers() -> None:
    source = "token=private-token Authorization: Bearer unknown-token password=hunter2"

    redacted = redact(source, {"private-token", "hunter2"})

    assert "private-token" not in redacted
    assert "unknown-token" not in redacted
    assert "hunter2" not in redacted
    assert redacted.count("[REDACTED]") == 3


def test_sandbox_event_command_is_scoped_to_exact_runtime_identity(tmp_path: Path) -> None:
    command = sandbox_event_command(_config(tmp_path))
    rendered = " ".join(command)

    assert command[:2] == ("docker", "events")
    assert "label=opencitadel.io/sandbox=true" in rendered
    assert "label=com.opencitadel.acceptance.project=opencitadel-acceptance-run-a" in rendered
    assert "label=com.opencitadel.acceptance.run=run-a" in rendered
    assert "{{json .}}" in command


def test_summarize_sandbox_events_counts_unique_complete_lifecycles() -> None:
    lines = [
        json.dumps({"Action": "create", "Actor": {"ID": "sandbox-a"}}),
        json.dumps({"Action": "start", "Actor": {"ID": "sandbox-a"}}),
        json.dumps({"Action": "create", "Actor": {"ID": "sandbox-b"}}),
        json.dumps({"Action": "destroy", "Actor": {"ID": "sandbox-a"}}),
        json.dumps({"Action": "destroy", "Actor": {"ID": "sandbox-b"}}),
        json.dumps({"Action": "destroy", "Actor": {"ID": "sandbox-b"}}),
    ]

    assert summarize_sandbox_events(lines) == SandboxEvidence(created=2, drained=2)

    with pytest.raises(ValueError, match="malformed"):
        summarize_sandbox_events(["not-json"])


def test_owned_resources_rejects_a_dynamic_sandbox_with_mismatched_run_identity(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    commands = FakeCommandRunner(config.evidence_dir, mismatched_dynamic_identity=True)
    commands.dynamic_present = True

    with pytest.raises(OwnershipError, match="does not match"):
        owned_resources(commands, config, REPOSITORY_ROOT)


def test_runner_success_writes_valid_evidence_and_removes_disposable_resources(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    commands = FakeCommandRunner(config.evidence_dir)
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )

    assert runner.execute() == 0
    manifest = json.loads((config.evidence_dir / "manifest.json").read_text())
    assert manifest["result"]["status"] == "passed"
    assert validate_manifest(manifest, config.evidence_dir) == []
    build_call = next(call for call in commands.calls if "build" in call)
    assert set(build_call[build_call.index("build") + 1 :]) == {
        "opencitadel-sandbox",
        "opencitadel-migrate",
        "opencitadel-api",
        "opencitadel-execution-kernel",
        "opencitadel-ui",
        "opencitadel-ops-collector",
        "opencitadel-ops-actuator",
        "acceptance-inference",
    }
    assert any("--volumes" in call for call in commands.calls if "down" in call)
    logs = (config.evidence_dir / "logs/stack.log").read_text()
    assert "acceptance-provider-token" not in logs
    assert "externally-visible-secret" not in logs


def test_runner_counts_sandbox_drained_before_final_snapshot_from_lifecycle_events(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    commands = FakeCommandRunner(
        config.evidence_dir,
        sandbox_drained_before_cleanup=True,
    )
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )

    assert runner.execute() == 0
    manifest = json.loads((config.evidence_dir / "manifest.json").read_text())

    assert manifest["sandboxes"] == {"created": 1, "drained": 1}
    lifecycle_calls = [call for call in commands.calls if call[:2] == ("docker", "events")]
    assert len(lifecycle_calls) == 2
    for call in lifecycle_calls:
        rendered = " ".join(call)
        assert "label=opencitadel.io/sandbox=true" in rendered
        assert "label=com.opencitadel.acceptance.project=opencitadel-acceptance-run-a" in rendered
        assert "label=com.opencitadel.acceptance.run=run-a" in rendered


def test_runner_preserves_and_reports_local_volumes_by_default(tmp_path: Path) -> None:
    config = _config(tmp_path, disposable=False)
    commands = FakeCommandRunner(config.evidence_dir, retain_volumes=True)
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )

    assert runner.execute() == 0
    manifest = json.loads((config.evidence_dir / "manifest.json").read_text())
    assert manifest["residue"]["volumes"] == 2
    assert manifest["residue"]["retained_volumes"] == [
        "retained-postgres",
        "retained-redis",
    ]
    assert not any("--volumes" in call for call in commands.calls if "down" in call)
    assert validate_manifest(manifest, config.evidence_dir) == []


def test_runner_playwright_failure_still_cleans_and_writes_valid_failure_evidence(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    commands = FakeCommandRunner(config.evidence_dir, playwright_exit=1)
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )

    assert runner.execute() == 1
    manifest = json.loads((config.evidence_dir / "manifest.json").read_text())
    assert manifest["result"]["status"] == "failed"
    assert "playwright" in manifest["result"]["failure_reason"]
    assert manifest["residue"]["containers"] == 0
    assert validate_manifest(manifest, config.evidence_dir) == []


def test_runner_cleans_owned_resources_when_compose_up_partially_starts_then_fails(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    commands = FakeCommandRunner(config.evidence_dir, up_failure=True)
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )

    assert runner.execute() == 1
    manifest = json.loads((config.evidence_dir / "manifest.json").read_text())

    assert any("down" in call for call in commands.calls)
    assert commands.dynamic_present is False
    assert manifest["residue"] == {
        "containers": 0,
        "dynamic_sandboxes": 0,
        "networks": 0,
        "retained_volumes": [],
        "volumes": 0,
    }
    assert "start stack" in manifest["result"]["failure_reason"]


def test_runner_converges_when_a_sandbox_appears_after_the_first_down(tmp_path: Path) -> None:
    config = _config(tmp_path)
    commands = FakeCommandRunner(
        config.evidence_dir,
        playwright_exit=1,
        late_sandbox_after_first_down=True,
    )
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )

    assert runner.execute() == 1
    manifest = json.loads((config.evidence_dir / "manifest.json").read_text())

    assert commands.down_calls == 2
    assert manifest["residue"] == {
        "containers": 0,
        "dynamic_sandboxes": 0,
        "networks": 0,
        "retained_volumes": [],
        "volumes": 0,
    }
    assert "owned runtime residue" not in manifest["result"]["failure_reason"]


def test_runner_failure_diagnostic_does_not_let_stderr_warning_hide_stdout_root_cause(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    commands = FakeCommandRunner(
        config.evidence_dir,
        playwright_exit=1,
        playwright_stdout="Error: authenticated request failed with HTTP 401",
        playwright_stderr="Warning: NO_COLOR was ignored",
    )
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )

    assert runner.execute() == 1
    manifest = json.loads((config.evidence_dir / "manifest.json").read_text())
    assert "authenticated request failed with HTTP 401" in manifest["result"]["failure_reason"]


def test_runner_project_filter_writes_successful_non_release_partial_scope(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    commands = FakeCommandRunner(config.evidence_dir)
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
        playwright_projects=("identity",),
    )

    assert runner.execute() == 0
    manifest = json.loads((config.evidence_dir / "manifest.json").read_text())
    assert manifest["scope"] == {
        "kind": "partial",
        "requested_projects": ["identity"],
    }
    assert {item["requirement_id"] for item in manifest["coverage"]} == set(
        ACCEPTANCE_PROJECT_REQUIREMENTS["identity"]
    )
    assert validate_manifest(manifest, config.evidence_dir) == []


def test_runner_readiness_fault_and_cancellation_both_execute_cleanup(tmp_path: Path) -> None:
    for run_id, fault in (("readiness", "readiness"), ("cancelled", "none")):
        config = _config(tmp_path / run_id)
        commands = FakeCommandRunner(config.evidence_dir)
        runner = AcceptanceRunner(
            config,
            commands=commands,
            repository_root=REPOSITORY_ROOT,
            readiness_probe=lambda _url: True,
            fault=fault,
        )
        if run_id == "cancelled":
            commands.on_up = runner.request_cancel

        assert runner.execute() == 1
        manifest = json.loads((config.evidence_dir / "manifest.json").read_text())
        assert manifest["result"]["status"] == "failed"
        assert any("down" in call for call in commands.calls)


def test_runner_publishes_stack_ready_atomically_and_honors_cancellation(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    commands = FakeCommandRunner(config.evidence_dir)
    observed: dict[str, object] = {}
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )

    def cancel_after_ready() -> None:
        observed.update(json.loads((config.evidence_dir / "lifecycle.json").read_text()))
        runner.request_cancel()

    commands.on_npm_ci = cancel_after_ready

    assert runner.execute() == 1
    manifest = json.loads((config.evidence_dir / "manifest.json").read_text())
    lifecycle = json.loads((config.evidence_dir / "lifecycle.json").read_text())

    assert observed["state"] == "stack_ready"
    assert observed["runner_pid"] == os.getpid()
    assert manifest["result"]["failure_reason"] == "cancelled: termination requested"
    assert lifecycle["state"] == "complete"
    assert lifecycle["result_status"] == "failed"
    assert not any(call[:2] == ("npx", "playwright") for call in commands.calls)


def test_runner_refuses_preexisting_exact_identity_without_mutating_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    commands = FakeCommandRunner(config.evidence_dir, collision=True)
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )

    assert runner.execute() == 1
    assert "preflight" in capsys.readouterr().err
    assert not any("up" in call or "down" in call for call in commands.calls)


def test_runner_reports_an_occupied_port_without_starting_resources(tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        occupied = listener.getsockname()[1]
        config = AcceptanceConfig(
            project_name="opencitadel-acceptance-run-a",
            run_id="run-a",
            disposable=True,
            evidence_dir=(tmp_path / "evidence").resolve(),
            base_url=f"http://127.0.0.1:{occupied}",
            ops_console_url="http://127.0.0.1:29099",
        )
        commands = FakeCommandRunner(config.evidence_dir)
        runner = AcceptanceRunner(
            config,
            commands=commands,
            repository_root=REPOSITORY_ROOT,
            readiness_probe=lambda _url: True,
        )

        assert runner.execute() == 1
        assert not any("up" in call or "down" in call for call in commands.calls)


def test_runner_never_overwrites_a_preexisting_evidence_directory(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.evidence_dir.mkdir(parents=True)
    sentinel = config.evidence_dir / "user-evidence.txt"
    sentinel.write_text("preserve me\n", encoding="utf-8")
    commands = FakeCommandRunner(config.evidence_dir)
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )

    assert runner.execute() == 1
    assert sentinel.read_text(encoding="utf-8") == "preserve me\n"
    assert not (config.evidence_dir / "manifest.json").exists()
    assert not any("up" in call or "down" in call for call in commands.calls)


def test_cleanup_failure_overrides_a_successful_test_result(tmp_path: Path) -> None:
    config = _config(tmp_path)
    commands = FakeCommandRunner(config.evidence_dir, cleanup_failure=True)
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )

    assert runner.execute() == 1
    manifest = json.loads((config.evidence_dir / "manifest.json").read_text())
    assert manifest["result"]["status"] == "failed"
    assert "cleanup" in manifest["result"]["failure_reason"]
