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


@pytest.fixture
def passed_capacity(monkeypatch):
    """Keep legacy orchestration tests focused on their fake successful dependencies.

    Actual capacity validation and missing-report failure have separate tests.
    This fixture does not change port probing or claim any benchmark execution.
    """
    monkeypatch.setattr(
        "scripts.acceptance.runner.prepare_capacity_evidence", lambda **kwargs: {"errors": []}
    )
    monkeypatch.setattr(AcceptanceRunner, "_prepare_strict_binding", lambda *args: None)
    monkeypatch.setattr(AcceptanceRunner, "_validate_strict_receipt", lambda *args: None)


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
        strict_pytest_xml: str | None = None,
        strict_pytest_exit: int = 0,
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
        self.strict_pytest_xml = strict_pytest_xml
        self.strict_pytest_exit = strict_pytest_exit
        self.strict_pytest_environment = None
        self.strict_pytest_cwd = None
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
        del timeout
        args = tuple(str(item) for item in args)
        self.calls.append(args)

        if args[:3] == (sys.executable, "-m", "pytest"):
            self.strict_pytest_environment = dict(env)
            self.strict_pytest_cwd = cwd
            output = Path(
                next(item.split("=", 1)[1] for item in args if item.startswith("--junitxml="))
            )
            output.write_text(
                self.strict_pytest_xml
                if self.strict_pytest_xml is not None
                else "<testsuites><testsuite>"
                + "".join(f'<testcase name="consumer-{index}"/>' for index in range(6))
                + "</testsuite></testsuites>",
                encoding="utf-8",
            )
            return self._result(args, returncode=self.strict_pytest_exit)

        if any("label=opencitadel.e04." in item for item in args):
            return self._result(args)

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
            if args[-1] == "kernel-id":
                import hashlib

                return self._result(
                    args,
                    stdout=json.dumps(
                        {
                            "Id": "kernel-id",
                            "Image": "sha256:"
                            + hashlib.sha256(b"opencitadel-execution-kernel").hexdigest(),
                            "Config": {
                                "Labels": {
                                    "com.docker.compose.project": "opencitadel-acceptance-run-a",
                                    "com.docker.compose.service": "opencitadel-execution-kernel",
                                    "com.opencitadel.acceptance.project": "opencitadel-acceptance-run-a",
                                    "com.opencitadel.acceptance.run": "run-a",
                                }
                            },
                            "State": {"Running": True},
                        }
                    )
                    + "\n",
                )
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
            if args[3].startswith("sha256:"):
                return self._result(args, stdout=args[3] + "\n")
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
                if "-q" in args and "opencitadel-execution-kernel" in args:
                    return self._result(args, stdout="kernel-id\n")
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
    passed_capacity,
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
        "opencitadel-sandbox-broker",
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


def test_execution_consumers_run_after_current_strict_receipt_and_before_capacity_failure(
    tmp_path, monkeypatch
):
    config = _config(tmp_path)
    commands = FakeCommandRunner(config.evidence_dir)
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )
    monkeypatch.setattr("scripts.acceptance.runner.assert_ports_available", lambda _ports: None)

    def prepare(*_args):
        runner._environment["ACCEPTANCE_STRICT_INVOCATION_ID"] = "current-unit-invocation"

    monkeypatch.setattr(runner, "_prepare_strict_binding", prepare)
    monkeypatch.setattr(
        runner,
        "_validate_strict_receipt",
        lambda: commands.calls.append(("strict-receipt-validated",)),
    )
    assert runner.execute() == 1
    pytest_call = next(
        call for call in commands.calls if call[:3] == (sys.executable, "-m", "pytest")
    )
    assert pytest_call[3:5] == (
        "-q",
        "tests/app/integration/test_execution_visualization_closed_loop.py",
    )
    assert commands.strict_pytest_cwd == REPOSITORY_ROOT / "api"
    environment = commands.strict_pytest_environment
    assert environment["ACCEPTANCE_EVIDENCE_DIR"] == str(config.evidence_dir)
    assert environment["ACCEPTANCE_STRICT_INVOCATION_ID"] == "current-unit-invocation"
    assert environment["ACCEPTANCE_RUN_ID"] == "run-a"
    assert environment["ACCEPTANCE_PROJECT_ID"] == "opencitadel-acceptance-run-a"
    assert commands.calls.index(("strict-receipt-validated",)) < commands.calls.index(pytest_call)
    playwright = next(call for call in commands.calls if call[:2] == ("npx", "playwright"))
    assert commands.calls.index(playwright) < commands.calls.index(("strict-receipt-validated",))
    manifest = json.loads((config.evidence_dir / "manifest.json").read_text())
    assert "AC21 capacity" in manifest["result"]["failure_reason"]
    assert manifest["residue"]["containers"] == 0


@pytest.mark.parametrize(
    ("xml", "status"),
    [
        ("<testsuites/>", 0),
        (
            "<testsuites><testsuite>"
            + "<testcase><skipped/></testcase>" * 6
            + "</testsuite></testsuites>",
            0,
        ),
        (
            "<testsuites><testsuite>"
            + "<testcase><failure/></testcase>" * 6
            + "</testsuite></testsuites>",
            0,
        ),
        (
            "<testsuites><testsuite>"
            + "<testcase><error/></testcase>" * 6
            + "</testsuite></testsuites>",
            0,
        ),
        ("<testsuites><testsuite>" + "<testcase/>" * 5 + "</testsuite></testsuites>", 0),
        ("not xml", 0),
        ("<testsuites><testsuite>" + "<testcase/>" * 6 + "</testsuite></testsuites>", 1),
    ],
)
def test_execution_consumers_require_all_six_passes_without_skips(
    tmp_path, passed_capacity, monkeypatch, xml, status
):
    monkeypatch.setattr("scripts.acceptance.runner.assert_ports_available", lambda _ports: None)
    config = _config(tmp_path)
    commands = FakeCommandRunner(
        config.evidence_dir, strict_pytest_xml=xml, strict_pytest_exit=status
    )
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )
    assert runner.execute() == 1
    manifest = json.loads((config.evidence_dir / "manifest.json").read_text())
    assert "strict execution consumers" in manifest["result"]["failure_reason"]
    assert any("down" in call for call in commands.calls)


def test_execution_consumers_refuse_existing_pytest_evidence(tmp_path):
    config = _config(tmp_path)
    config.evidence_dir.mkdir()
    (config.evidence_dir / "strict-pytest.xml").write_text("<testsuites/>")
    commands = FakeCommandRunner(config.evidence_dir)
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )
    with pytest.raises(RuntimeError, match="current invocation pytest evidence already exists"):
        runner._run_strict_consumers()
    assert commands.calls == []


def test_runner_counts_sandbox_drained_before_final_snapshot_from_lifecycle_events(
    tmp_path: Path,
    passed_capacity,
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


def test_runner_preserves_and_reports_local_volumes_by_default(
    tmp_path: Path, passed_capacity
) -> None:
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
    assert not any(call[:3] == (sys.executable, "-m", "pytest") for call in commands.calls)


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


def test_acceptance_runner_uses_scoped_loopback_compose_override(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = AcceptanceRunner(
        config,
        commands=FakeCommandRunner(config.evidence_dir),
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )
    args = runner._compose
    assert args[args.index("-f") + 1] == "docker-compose.yml"
    assert "scripts/acceptance/compose.loopback.yml" in args
    override = (REPOSITORY_ROOT / "scripts/acceptance/compose.loopback.yml").read_text()
    assert override.count("!override") == 2
    assert "127.0.0.1:${NGINX_PORT:-8088}:80" in override
    assert "127.0.0.1:${NGINX_HTTPS_PORT:-443}:443" in override
    assert "127.0.0.1:${OPS_CONSOLE_PORT:-9099}:9099" in override


@pytest.mark.parametrize("mismatch", ["project", "run", "namespace", "id"])
def test_e04_inventory_rejects_partial_ownership(tmp_path, mismatch):
    from scripts.acceptance.runner import _evaluation_identity

    class Commands:
        def run(self, args, **kwargs):
            labels = {
                "opencitadel.e04.acceptance.project": "opencitadel-acceptance-run-a",
                "opencitadel.e04.acceptance.run": "run-a",
                "opencitadel.e04.namespace": "e04-" + "a" * 40,
                "opencitadel.e04.lease": "f1d98682-a332-4539-9989-a187443d5843",
                "opencitadel.e04.generation": "1",
                "opencitadel.e04.role": "case",
            }
            if mismatch in {"project", "run"}:
                labels["opencitadel.e04.acceptance." + mismatch] = "foreign"
            elif mismatch == "namespace":
                labels["opencitadel.e04.namespace"] = "unowned"
            return CommandResult(
                tuple(args),
                0,
                json.dumps(
                    {"Id": "foreign" if mismatch == "id" else "owned", "Config": {"Labels": labels}}
                ),
                "",
            )

    with pytest.raises(OwnershipError):
        _evaluation_identity(Commands(), _config(tmp_path), tmp_path, "container", "owned")


def test_acceptance_inventory_uses_captured_sandbox_and_fixed_fixture(tmp_path):
    from scripts.acceptance.manifest import ImageEvidence

    commands = FakeCommandRunner(tmp_path / "evidence")
    runner = AcceptanceRunner(
        _config(tmp_path),
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda url: True,
    )
    runner.config.evidence_dir.mkdir()
    sandbox_id = "sha256:" + "a" * 64
    runner._write_evaluation_inventory(
        ImageEvidence(production={"sandbox": sandbox_id}, acceptance_provider="sha256:" + "b" * 64)
    )
    environment = json.loads(
        (runner.config.evidence_dir / "evaluation-environment.json").read_text()
    )
    budget = json.loads((runner.config.evidence_dir / "evaluation-budget.json").read_text())
    assert environment["images"] == [sandbox_id]
    assert environment["fixture_image"] == sandbox_id
    assert len(environment["targets"]) == 1
    assert budget == {"revision": "acceptance-fixture-v1", "acceptance_fixture": True}
    assert runner._environment["EVALUATION_ACCEPTANCE_OWNER"] == runner.config.project_name


@pytest.mark.parametrize(("disposable", "fails"), [(True, False), (True, True), (False, False)])
def test_e04_teardown_is_exact_ordered_and_reports_failures(tmp_path, disposable, fails):
    class EvaluationCommands(FakeCommandRunner):
        def __init__(self, directory):
            super().__init__(directory)
            self.evaluation = {"container": True, "network": True}

        def run(self, args, **kwargs):
            args = tuple(args)
            if args[:3] in (("docker", "container", "ls"), ("docker", "network", "ls")) and any(
                "label=opencitadel.e04." in item for item in args
            ):
                self.calls.append(args)
                return self._result(
                    args, stdout=args[1] + "-owned\n" if self.evaluation[args[1]] else ""
                )
            if args[:3] in (("docker", "container", "inspect"), ("docker", "network", "inspect")):
                self.calls.append(args)
                labels = {
                    "opencitadel.e04.acceptance.project": "opencitadel-acceptance-run-a",
                    "opencitadel.e04.acceptance.run": "run-a",
                    "opencitadel.e04.namespace": "e04-" + "a" * 40,
                    "opencitadel.e04.lease": "f1d98682-a332-4539-9989-a187443d5843",
                    "opencitadel.e04.generation": "1",
                    "opencitadel.e04.role": "case",
                }
                body = (
                    {"Id": args[-1], "Config": {"Labels": labels}}
                    if args[1] == "container"
                    else {"Id": args[-1], "Labels": labels}
                )
                return self._result(args, stdout=json.dumps(body))
            if args[:3] in (("docker", "container", "rm"), ("docker", "network", "rm")):
                self.calls.append(args)
                if fails:
                    return self._result(args, returncode=1, stderr="physical removal failed")
                if args[1] == "network":
                    assert not self.evaluation["container"]
                self.evaluation[args[1]] = False
                return self._result(args)
            return super().run(args, **kwargs)

    config = _config(tmp_path, disposable=disposable)
    config.evidence_dir.mkdir()
    commands = EvaluationCommands(config.evidence_dir)
    runner = AcceptanceRunner(
        config, commands=commands, repository_root=REPOSITORY_ROOT, readiness_probe=lambda url: True
    )
    residue, errors = runner._cleanup()
    removals = [
        call
        for call in commands.calls
        if call[:3] in (("docker", "container", "rm"), ("docker", "network", "rm"))
    ]
    if disposable and not fails:
        assert residue.empty
        assert not errors
        assert [call[1] for call in removals] == ["container", "network"]
    else:
        assert residue.evaluation_containers == ("container-owned",)
        assert residue.evaluation_networks == ("network-owned",)
        assert errors
        if not disposable:
            assert not removals
    evidence = json.loads((config.evidence_dir / "evaluation-teardown-after.json").read_text())
    assert evidence["authoritative_lease_state_changed"] is False
    assert bool(evidence["errors"]) == bool(errors)


def test_missing_capacity_fails_even_if_browser_claims_pass_and_still_cleans(tmp_path, monkeypatch):
    import scripts.acceptance.runner as module

    # This unit checks orchestration only. No socket, subprocess or service runs.
    monkeypatch.setattr(module, "assert_ports_available", lambda _ports: None)
    config = _config(tmp_path)
    commands = FakeCommandRunner(config.evidence_dir)
    runner = AcceptanceRunner(
        config,
        commands=commands,
        repository_root=REPOSITORY_ROOT,
        readiness_probe=lambda _url: True,
    )
    monkeypatch.setattr(runner, "_prepare_strict_binding", lambda *args: None)
    monkeypatch.setattr(runner, "_validate_strict_receipt", lambda: None)
    runner._environment.pop("ACCEPTANCE_CAPACITY_REPORT", None)
    runner._environment.pop("ACCEPTANCE_CAPACITY_FIXTURE_MANIFEST", None)
    assert runner.execute() == 1
    receipt = json.loads((config.evidence_dir / "capacity-validation.json").read_text())
    assert receipt["run_id"] == config.run_id
    assert receipt["project"] == config.project_name
    assert receipt["errors"]
    manifest = json.loads((config.evidence_dir / "manifest.json").read_text())
    assert "AC21 capacity" in manifest["result"]["failure_reason"]
    assert any("down" in call for call in commands.calls)
    assert any(call[:2] == ("npx", "playwright") for call in commands.calls)
    assert manifest["residue"]["containers"] == 0
