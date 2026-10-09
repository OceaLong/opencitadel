"""Explicit bounded host arm/snapshot/disarm, never a watcher or public endpoint."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import UUID

from scripts.acceptance.physical_faults import digest, source_digest
from scripts.acceptance.strict_bridge import assert_kernel_identity, atomic_json, read_json


def invoke(
    action,
    *,
    root,
    execution_run_id=None,
    activity_id=None,
    kind=None,
    version_id=None,
    fault_id=None,
    execute=subprocess.run,
):
    root = Path(root)
    binding, bootstrap = (
        read_json(root / "strict-binding.json"),
        read_json(root / "strict-bootstrap.json"),
    )
    if (binding["run_id"], binding["project"], binding["invocation_id"]) != (
        os.environ["ACCEPTANCE_RUN_ID"],
        os.environ["ACCEPTANCE_PROJECT_ID"],
        os.environ["ACCEPTANCE_STRICT_INVOCATION_ID"],
    ):
        raise ValueError("foreign fault invocation")
    if action not in {"arm", "snapshot", "disarm", "cancel"}:
        raise ValueError("invalid fault action")
    for value in (execution_run_id, activity_id, version_id, fault_id):
        if value is not None:
            UUID(value)
    container = binding["kernel_container"]

    started = time.monotonic()
    deadline = started + (240 if action == "arm" else 90)
    acquisition_deadline = started + 180

    def remaining(limit):
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise ValueError("bounded fault action timed out; obligations unresolved")
        return min(limit, seconds)

    def inspect():
        raw = execute(
            ["docker", "inspect", container], check=True, capture_output=True, timeout=remaining(30)
        ).stdout
        assert_kernel_identity(json.loads(raw)[0], binding, running=True)

    inspect()
    data = {
        "action": action,
        "project": binding["project"],
        "run": binding["run_id"],
        "owner_user_id": bootstrap["operator_id"],
        "source_sha256": source_digest(),
        "invocation_id": binding["invocation_id"],
        "binding_sha256": digest(binding),
        "kernel_container": container,
        "execution_run_id": execution_run_id,
        "activity_id": activity_id,
        "kind": kind,
        "version_id": version_id,
        "fault_id": fault_id,
    }
    while True:
        try:
            result = execute(
                [
                    "docker",
                    "exec",
                    "-i",
                    container,
                    "/app/.venv/bin/python",
                    "/acceptance-driver/physical_fault_command.py",
                ],
                input=json.dumps(data).encode(),
                check=True,
                capture_output=True,
                timeout=remaining(90),
            )
        except subprocess.CalledProcessError as error:
            # Keep the bound command's private diagnostic out of browser output.
            path = root / f"physical-fault-{action}-kernel-stderr.log"
            raw = error.stderr or b""
            if isinstance(raw, str):
                raw = raw.encode()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(raw[:262144])
            raise
        inspect()
        if action != "arm" or json.loads(result.stdout) != {"busy": True}:
            break
        if time.monotonic() >= acquisition_deadline:
            raise ValueError("fault arm acquisition timed out; no operation approved")
        time.sleep(0.5)
        inspect()
    if len(result.stdout) > 262144:
        raise ValueError("oversized fault receipt")
    receipt = json.loads(result.stdout)
    UUID(receipt["fault_id"])
    if fault_id is not None and receipt["fault_id"] != fault_id:
        raise ValueError("foreign fault receipt")
    atomic_json(
        root / f"fault-{receipt['fault_id']}-{action}.json",
        {"binding": binding, "receipt": receipt},
    )
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["arm", "snapshot", "disarm", "cancel"])
    for name in ("execution-run-id", "activity-id", "kind", "version-id", "fault-id"):
        parser.add_argument("--" + name)
    args = vars(parser.parse_args())
    receipt = invoke(root=os.environ["ACCEPTANCE_EVIDENCE_DIR"], **args)
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
