"""Host-only strict-driver bridge invoked by the serial acceptance bootstrap.

No production endpoint or installed test dependency. Hashes bind local artifacts;
they are not signatures and do not establish scientific truth of assertions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
SCENARIOS = {
    "AC02": {"parallel_order", "missing_parent", "business_failure_successful_run"},
    "AC05": {"shadow_activation", "missing_history_available_segment"},
    "AC12": {"reset_failure", "worker_death", "late_completion_cancel"},
    "AC13": {
        "concurrent_submit",
        "admission_restart",
        "lease_transfer",
        "budget_exhaustion",
        "scheduler_budget_stop",
    },
    "AC19": {"seven_daily_buckets"},
    "AC22": {"legacy_coexistence", "upcast_hash_replay", "old_entrypoint"},
}


class BridgeError(RuntimeError):
    pass


def validate_scheduler_budget(scenario):
    """Validate source-backed positive spend and the distinct unadmitted next slot."""
    before, after = scenario.get("before", {}), scenario.get("after", {})
    ids = scenario.get("resource_ids", {})
    bound, used = before.get("subject_bound"), after.get("usage", {}).get("total_tokens")
    ledger = after.get("ledger", {})
    if (
        type(bound) is not int
        or bound <= 0
        or type(used) is not int
        or used <= 0
        or before.get("token_budget") != bound
        or type(before.get("judge_bound")) is not int
        or not 0 < before["judge_bound"] <= bound
        or used > bound
        or before.get("preflight_allowed") is not True
        or type(before.get("preflight_revision")) is not int
        or before["preflight_revision"] < 1
        or before.get("repeat") != 2
        or before.get("dispatch_limit") != 1
        or after.get("physical_sends") != 1
        or after.get("settlements") != 1
        or ledger.get("spent_tokens") != used
        or ledger.get("reserved_tokens") != 0
        or ledger.get("slots") != 0
        or after.get("execution_status") != "blocked_budget"
        or after.get("scoring_status") != "skipped"
        or any(
            after.get(key) is not False
            for key in ("envelope", "prepared_envelope", "admission_receipt")
        )
        or not all(
            ids.get(key)
            for key in (
                "batch_id",
                "suite_version",
                "first_result_id",
                "blocked_result_id",
                "run_id",
                "call_identity",
            )
        )
        or ids.get("first_result_id") == ids.get("blocked_result_id")
    ):
        raise BridgeError(
            "scheduler budget evidence lacks accepted bound, real spend or unadmitted result"
        )


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, body):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(body, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_json(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise BridgeError("unsafe or oversized strict artifact")
    return json.loads(path.read_text())


def assert_kernel_identity(document, binding, *, running=None):
    expected = {
        "com.docker.compose.project": binding["project"],
        "com.docker.compose.service": "opencitadel-execution-kernel",
        "com.opencitadel.acceptance.project": binding["project"],
        "com.opencitadel.acceptance.run": binding["run_id"],
    }
    try:
        if (
            document["Id"] != binding["kernel_container"]
            or document["Image"] != binding["kernel_image"]
        ):
            raise BridgeError("kernel container/image was replaced")
        if any(document["Config"]["Labels"].get(key) != value for key, value in expected.items()):
            raise BridgeError("kernel ownership does not match invocation")
        if running is not None and document["State"]["Running"] is not running:
            raise BridgeError("kernel running state does not match exclusive stage")
    except (KeyError, TypeError) as exc:
        raise BridgeError("incomplete kernel ownership") from exc


def driver_argv(compose, driver_root, input_path, binding):
    # Compose service image is checked against the immutable image before AND
    # after launch; --pull never prevents a mutable remote tag replacement.
    return (
        *compose,
        "--file",
        str(Path(input_path).resolve().with_name("strict-image.json")),
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--pull",
        "never",
        "--name",
        f"{binding['project']}-strict-{binding['invocation_id'].replace('-', '')}",
        "--volume",
        f"{Path(driver_root).resolve()}:/acceptance-driver:ro",
        "--volume",
        f"{Path(input_path).resolve()}:/acceptance-input.json:ro",
        "--entrypoint",
        "/app/.venv/bin/python",
        "opencitadel-execution-kernel",
        "/acceptance-driver/strict_driver/main.py",
        "--input",
        "/acceptance-input.json",
    )


@contextmanager
def quiesced(binding, *, run, inspect, ready):
    assert_kernel_identity(inspect(), binding, running=True)
    original = None
    try:
        # Ambiguous stop is also restored; entering try before stop is essential.
        run(("docker", "stop", "--time", "45", binding["kernel_container"]))
        yield
    except BaseException as exc:
        original = exc
        raise
    finally:
        failures = []
        try:
            assert_kernel_identity(inspect(), binding)
            run(("docker", "start", binding["kernel_container"]))
        except BaseException as exc:  # noqa: BLE001 - restore even on cancellation
            failures.append(exc)
        try:
            ready()
        except BaseException as exc:  # noqa: BLE001 - restore even on cancellation
            failures.append(exc)
        if failures:
            if original is not None:
                failures.insert(0, original)
            raise BaseExceptionGroup("strict driver and kernel restoration failures", failures)


def validate_evidence(report, binding, bootstrap, artifact_root):
    if report.get("schema_version") != 1 or report.get("binding") != binding:
        raise BridgeError("strict evidence stale/foreign binding")
    if (
        report.get("bootstrap_sha256")
        != hashlib.sha256(
            json.dumps(bootstrap, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    ):
        raise BridgeError("strict evidence has different bootstrap identities")
    if report.get("status") != "passed" or report.get("errors") != []:
        raise BridgeError("strict driver incomplete/failed/interrupted")
    seen = set()
    artifacts = report.get("artifacts", [])
    if not artifacts:
        raise BridgeError("strict evidence has no raw artifacts")
    raw = {}
    for artifact in artifacts:
        name = artifact.get("path", "")
        if not re.fullmatch(r"strict-[a-z0-9-]+\.json", name) or name in raw:
            raise BridgeError("unsafe/duplicate raw artifact")
        path = Path(artifact_root) / name
        document = read_json(path)
        if digest(path) != artifact.get("sha256") or document.get("binding") != binding:
            raise BridgeError("raw artifact digest/origin mismatch")
        if name == "strict-legacy-raw.json":
            from scripts.acceptance.strict_legacy import LEGACY_REVISION

            legacy = document.get("legacy", {})
            if (
                legacy.get("historical_revision") != LEGACY_REVISION
                or legacy.get("main_database_distinct") is not True
            ):
                raise BridgeError("historical fixture origin mismatch")
            for filename, key in (
                ("strict-legacy-source.tar", "source_archive_sha256"),
                ("strict-legacy-before.json", "before_sha256"),
                ("strict-legacy-after.json", "after_sha256"),
            ):
                dependency = Path(artifact_root) / filename
                if (
                    dependency.is_symlink()
                    or not dependency.is_file()
                    or digest(dependency) != document.get(key)
                ):
                    raise BridgeError("historical dependency digest mismatch")
        raw[name] = document
    for scenario in report.get("scenarios", []):
        identity = (scenario.get("requirement"), scenario.get("id"))
        if identity in seen or identity[1] not in SCENARIOS.get(identity[0], set()):
            raise BridgeError("duplicate/unrecognized scenario")
        if identity == ("AC13", "scheduler_budget_stop"):
            validate_scheduler_budget(scenario)
        seen.add(identity)
        if scenario.get("status") != "passed" or not scenario.get("test_id"):
            raise BridgeError("scenario not passed or missing test identity")
        if not scenario.get("resource_ids") or not scenario.get("assertions"):
            raise BridgeError("scenario lacks exact resources/assertions")
        if any(
            item.get("passed") is not True or not item.get("id") for item in scenario["assertions"]
        ):
            raise BridgeError("scenario assertion failed/missing")
        historical = identity[0] == "AC22" or identity[1] == "missing_history_available_segment"
        expected_artifact = "strict-legacy-raw.json" if historical else "strict-raw.json"
        if scenario.get("artifact") != expected_artifact:
            raise BridgeError("scenario producer origin mismatch")
        if historical and (
            scenario.get("database") != raw.get(expected_artifact, {}).get("legacy")
            or scenario.get("cleanup", {}).get("state") != "disposed"
        ):
            raise BridgeError("historical scenario ownership/disposal missing")
        source = raw.get(scenario.get("artifact"))
        if source is None or scenario not in source.get("scenarios", []):
            raise BridgeError("scenario is not backed by raw driver output")
        if not scenario.get("before") or not scenario.get("after") or not scenario.get("cleanup"):
            raise BridgeError("scenario lacks observations/cleanup obligations")
        if (
            identity[1] == "worker_death"
            and scenario.get("fault", {}).get("mechanism") != "child_process_termination"
        ):
            raise BridgeError("clock/exception seam is not process death")
    required = {(ac, scenario) for ac, scenarios in SCENARIOS.items() for scenario in scenarios}
    if seen != required:
        raise BridgeError("strict evidence missing scenarios: " + str(sorted(required - seen)))
    return {
        "schema_version": 1,
        "status": "passed",
        "binding": binding,
        "requirements": sorted(SCENARIOS),
        "raw_artifacts": artifacts,
    }


def journal_owned(evidence, binding, value):
    """Journal archiveable parents; keep strict batches in disposable evidence."""
    from uuid import UUID, uuid4

    # The strict budget scenario intentionally preserves an unknown physical
    # effect. E12 correctly refuses to archive that batch, so its durable
    # ownership record is retained until the exact disposable DB is removed.
    if value.get("kind") != "suite":
        return
    identity = str(UUID(value["id"]))
    root = Path(evidence) / "cleanup-journal" / "pending"
    root.mkdir(parents=True, exist_ok=True)
    order = str(time.monotonic_ns()).zfill(20)
    atomic_json(
        root / f"{order}-{uuid4()}.json",
        {
            "schema_version": 1,
            "run_id": binding["run_id"],
            "order": order,
            "value": {
                "action": "delete-resource",
                "resource": "evaluation-" + value["kind"],
                "resource_id": identity,
            },
        },
    )


def record_owned(evidence, binding, value):
    from uuid import UUID

    kind, identity = value.get("kind"), value.get("id")
    if (
        kind
        not in {
            "batch",
            "batch_request",
            "suite",
            "suite_version",
            "run",
            "result",
            "lease",
            "operation",
            "activity",
            "physical_call",
        }
        or not isinstance(identity, str)
        or not identity
        or len(identity) > 255
    ):
        raise BridgeError("invalid ownership record")
    if kind != "physical_call":
        UUID(identity)
    scope = value.get("scope", {})
    bootstrap = read_json(Path(evidence) / "strict-bootstrap.json")
    if scope != bootstrap.get("scope"):
        raise BridgeError("foreign ownership scope")
    path = Path(evidence) / "strict-owned.ndjson"
    with path.open("a", encoding="utf8") as stream:
        os.chmod(path, 0o600)
        stream.write(json.dumps({"binding": binding, "value": value}, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    journal_owned(evidence, binding, value)


def stop_owned_driver(run, binding):
    name = f"{binding['project']}-strict-{binding['invocation_id'].replace('-', '')}"
    # Query absence without turning an already auto-removed container into error.
    listed = run(("docker", "ps", "-aq", "--no-trunc", "--filter", "name=^/" + name + "$"))
    ids = listed.stdout.split()
    if not ids:
        return
    if len(ids) != 1:
        raise BridgeError("ambiguous strict child identity")
    document = json.loads(run(("docker", "inspect", "--format", "{{json .}}", ids[0])).stdout)
    expected = {**binding, "kernel_container": ids[0]}
    assert_kernel_identity(document, expected)
    if document.get("Name", "").lstrip("/") != name:
        raise BridgeError("strict container name mismatch")
    run(("docker", "stop", "--time", "5", ids[0]))
    # --rm normally removes it; no broad prune or prefix cleanup.


def stream_driver(argv, *, cwd, environment, evidence, binding, cleanup, timeout=600):
    """Durable ownership ACK before the producer performs its next operation."""
    reports = []
    total = 0
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    original = None
    try:
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        buffer = b""
        with selector:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BridgeError("strict driver timed out; ownership retained")
                if not selector.select(min(remaining, 0.25)):
                    if process.poll() is not None:
                        break
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ARTIFACT_BYTES:
                    raise BridgeError("strict driver output exceeded limit")
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    record = json.loads(line)
                    if record.get("kind") == "owned":
                        record_owned(evidence, binding, record["body"])
                        process.stdin.write(b"owned-durable\n")
                        process.stdin.flush()
                    elif record.get("kind") == "report":
                        reports.append(record["body"])
                    else:
                        raise BridgeError("unrecognized driver output")
            if buffer or len(reports) != 1:
                raise BridgeError("interrupted or missing terminal driver report")
        returncode = process.wait(timeout=max(1, deadline - time.monotonic()))
        return reports[0], returncode
    except BaseException as exc:
        original = exc
        raise
    finally:
        failures = []
        try:
            cleanup()  # Stop exact owned one-off before normal kernel restoration.
        except BaseException as exc:  # noqa: BLE001 - restore even on cancellation
            failures.append(exc)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        process.stdin.close()
        process.stdout.close()
        if failures:
            raise BaseExceptionGroup(
                "driver interruption and cleanup failures",
                ([original] if original else []) + failures,
            )


def run_bridge(repository_root, evidence_root, *, environment=None):
    """Fixed host operation; caller cannot supply shell, service, or command names."""
    from scripts.acceptance.runner import (
        AcceptanceConfig,
        AcceptanceRunner,
        SubprocessCommandRunner,
        _probe,
        capture_git_evidence,
    )

    environment = dict(os.environ if environment is None else environment)
    root, evidence = Path(repository_root).resolve(), Path(evidence_root).resolve()
    binding = read_json(evidence / "strict-binding.json")
    if binding["run_id"] != environment.get("ACCEPTANCE_RUN_ID") or binding[
        "project"
    ] != environment.get("ACCEPTANCE_PROJECT_ID"):
        raise BridgeError("host invocation does not match runner")
    bootstrap = read_json(evidence / "strict-bootstrap.json")
    from scripts.acceptance.strict_driver.contracts import DriverInput

    DriverInput.model_validate({"binding": binding, "bootstrap": bootstrap})
    if (
        bootstrap.get("run_id") != binding["run_id"]
        or bootstrap.get("project") != binding["project"]
    ):
        raise BridgeError("bootstrap belongs to another run/project")
    commands = SubprocessCommandRunner()
    actual = capture_git_evidence(commands, root)
    if (
        actual.revision != binding["revision"]
        or actual.dirty_tree_digest != binding["dirty_tree_digest"]
    ):
        raise BridgeError("source changed after image build")
    for name, key in (
        ("evaluation-environment.json", "inventory_sha256"),
        ("evaluation-budget.json", "budget_inventory_sha256"),
    ):
        if digest(evidence / name) != binding[key]:
            raise BridgeError("acceptance inventory changed")
    config = AcceptanceConfig(
        binding["project"],
        binding["run_id"],
        False,
        evidence,
        environment["PLAYWRIGHT_BASE_URL"],
        environment["OPS_CONSOLE_URL"],
    )
    runner = AcceptanceRunner(
        config, commands=commands, repository_root=root, readiness_probe=_probe
    )

    def run(args):
        result = commands.run(args, cwd=root, env=runner._environment, timeout=600)
        if result.returncode:
            # Do not put subprocess payloads/credentials into browser output.
            raise BridgeError(f"strict host operation failed: {args[1]} ({result.returncode})")
        return result

    def inspect():
        return json.loads(
            run(("docker", "inspect", "--format", "{{json .}}", binding["kernel_container"])).stdout
        )

    def ready():
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            current = inspect()
            assert_kernel_identity(current, binding)
            if (
                current["State"].get("Running")
                and current["State"].get("Health", {}).get("Status") == "healthy"
            ):
                runner._wait_ready()
                return
            time.sleep(0.25)
        raise BridgeError("exact kernel did not become healthy after strict driver")

    document = {"binding": binding, "bootstrap": bootstrap}
    atomic_json(evidence / "strict-input.json", document)
    atomic_json(
        evidence / "strict-image.json",
        {"services": {"opencitadel-execution-kernel": {"image": binding["kernel_image"]}}},
    )
    try:
        with quiesced(binding, run=run, inspect=inspect, ready=ready):
            assert_kernel_identity(inspect(), binding, running=False)
            if runner._capture_capacity_migration() != binding["migration"]:
                raise BridgeError("schema changed after runner binding")
            image = run(
                (
                    "docker",
                    "image",
                    "inspect",
                    "opencitadel-execution-kernel",
                    "--format",
                    "{{.Id}}",
                )
            )
            if image.stdout.strip() != binding["kernel_image"]:
                raise BridgeError("kernel service image changed")
            report, returncode = stream_driver(
                driver_argv(
                    runner._compose,
                    root / "scripts/acceptance",
                    evidence / "strict-input.json",
                    binding,
                ),
                cwd=root,
                environment=runner._environment,
                evidence=evidence,
                binding=binding,
                cleanup=lambda: stop_owned_driver(run, binding),
            )
            atomic_json(evidence / "strict-raw.json", report)
            if returncode:
                raise BridgeError("strict driver failed; bounded public evidence retained")
            report["artifacts"] = [
                {"path": "strict-raw.json", "sha256": digest(evidence / "strict-raw.json")}
            ]
            from scripts.acceptance.strict_legacy import run_legacy_fixture

            legacy_scenarios, legacy_artifact = run_legacy_fixture(
                root=root, evidence=evidence, binding=binding, runner=runner, run=run
            )
            report["scenarios"].extend(legacy_scenarios)
            report["artifacts"].append(legacy_artifact)
            atomic_json(evidence / "strict-report.json", report)
            receipt = validate_evidence(report, binding, bootstrap, evidence)
        receipt["kernel_restored"] = True
        atomic_json(evidence / "strict-consumer.json", receipt)
        return receipt
    except BaseException as exc:
        atomic_json(
            evidence / "strict-consumer.json",
            {
                "schema_version": 1,
                "binding": binding,
                "status": "failed",
                "error_type": type(exc).__name__,
            },
        )
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()  # No arbitrary paths or operations accepted from browser.
    if args.validate_only:
        root = Path(os.environ["ACCEPTANCE_EVIDENCE_DIR"])
        binding = read_json(root / "strict-binding.json")
        if (binding["run_id"], binding["project"], binding["invocation_id"]) != (
            os.environ["ACCEPTANCE_RUN_ID"],
            os.environ["ACCEPTANCE_PROJECT_ID"],
            os.environ["ACCEPTANCE_STRICT_INVOCATION_ID"],
        ):
            raise BridgeError("consumer invocation is stale or foreign")
        receipt = validate_evidence(
            read_json(root / "strict-report.json"),
            binding,
            read_json(root / "strict-bootstrap.json"),
            root,
        )
        if read_json(root / "strict-consumer.json") != {**receipt, "kernel_restored": True}:
            raise BridgeError("kernel restoration receipt is missing or changed")
        return
    run_bridge(Path(__file__).resolve().parents[2], os.environ["ACCEPTANCE_EVIDENCE_DIR"])


if __name__ == "__main__":
    main()
