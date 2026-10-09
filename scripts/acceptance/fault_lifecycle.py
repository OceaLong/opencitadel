"""Host-only private lifecycle serialization for opt-in parallel fault consumers.

Coordination busy is not a fault-control state. Never steal a failed/dead owner,
never unlock on cancellation, and never infer successful disarm from absence.
"""

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from uuid import UUID

from scripts.acceptance.physical_faults import FaultControl, FaultError, digest, source_digest
from scripts.acceptance.strict_bridge import read_json


class LifecycleError(FaultError):
    pass


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError as error:
        raise LifecycleError("coordinator owner liveness unavailable") from error
    return True


class Lifecycle:
    def __init__(self, root, binding, *, alive=process_alive):
        root = Path(root)
        if not root.is_absolute() or root.resolve() != root:
            raise LifecycleError("unsafe coordinator path")
        self.control = FaultControl(root)
        self.binding, self.alive = binding, alive

    def acquire(self, token, pid):
        UUID(token)
        if type(pid) is not int or pid <= 0:
            raise LifecycleError("invalid owner process")
        with self.control.lock():
            current = self.control.read("owner.json")
            if current is not None:
                if (
                    current.get("binding") != self.binding
                    or current.get("state") != "held"
                    or not self.alive(current["pid"])
                ):
                    raise LifecycleError("failed dead or foreign coordinator retained")
                if current["token"] == token:
                    raise LifecycleError("duplicate lifecycle acquisition")
                return {"busy": True}
            self.control.write(
                "owner.json",
                {
                    "binding": self.binding,
                    "token": token,
                    "pid": pid,
                    "state": "held",
                    "acquired_ns": time.time_ns(),
                },
            )
            return {"acquired": True}

    def owned(self, token):
        current = self.control.read("owner.json")
        if (
            current is None
            or current.get("binding") != self.binding
            or current.get("token") != token
            or current.get("state") != "held"
        ):
            raise LifecycleError("lifecycle ownership unavailable")
        return current

    def bind_fault(self, token, fault_id, *, receipt_time_ns):
        UUID(fault_id)
        with self.control.lock():
            current = self.owned(token)
            if current.get("fault_id") or receipt_time_ns < current["acquired_ns"]:
                raise LifecycleError("stale or duplicate arm receipt")
            self.control.write("owner.json", {**current, "fault_id": fault_id})

    def fail(self, token):
        with self.control.lock():
            current = self.owned(token)
            self.control.write("owner.json", {**current, "state": "failed"})

    def release(self, token, receipt):
        with self.control.lock():
            current = self.owned(token)
            disarms = [row for row in receipt.get("journal", []) if row.get("event") == "disarm"]
            if (
                receipt.get("cleanup") != "control removed"
                or not current.get("fault_id")
                or receipt.get("fault_id") != current["fault_id"]
                or len(disarms) != 1
                or disarms[0].get("fault_id") != receipt["fault_id"]
                or disarms[0].get("complete") is not True
            ):
                raise LifecycleError("positive exact successful disarm required")
            self.control.write(
                "released.json",
                {"binding": self.binding, "token": token, "fault_id": receipt["fault_id"]},
            )
            (self.control.root / "owner.json").unlink()
            fd = os.open(self.control.root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["acquire", "bind", "release", "fail"])
    parser.add_argument("--token", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--fault-id")
    args = parser.parse_args()
    evidence = Path(os.environ["ACCEPTANCE_EVIDENCE_DIR"]).resolve()
    binding = read_json(evidence / "strict-binding.json")
    if (binding["run_id"], binding["project"], binding["invocation_id"]) != (
        os.environ["ACCEPTANCE_RUN_ID"],
        os.environ["ACCEPTANCE_PROJECT_ID"],
        os.environ["ACCEPTANCE_STRICT_INVOCATION_ID"],
    ):
        raise LifecycleError("foreign lifecycle invocation")
    identity = {"binding": binding, "source_sha256": source_digest()}
    # Key by invocation, not source/binding: changes must encounter and reject
    # existing ownership instead of opening a new independent mutex.
    name = digest([os.getuid(), binding["project"], binding["run_id"], binding["invocation_id"]])
    root = Path(tempfile.gettempdir()).resolve() / ("opencitadel-fault-lifecycle-" + name)
    if root.is_relative_to(evidence) or evidence.is_relative_to(root):
        raise LifecycleError("coordinator must remain outside published evidence")
    gate = Lifecycle(root, identity)
    if args.action == "acquire":
        result = gate.acquire(args.token, args.pid)
    elif args.action == "fail":
        gate.fail(args.token)
        result = {"retained": True}
    else:
        fault = str(UUID(args.fault_id))
        path = evidence / f"fault-{fault}-{'arm' if args.action == 'bind' else 'disarm'}.json"
        record = read_json(path)
        if record.get("binding") != binding or record.get("receipt", {}).get("fault_id") != fault:
            raise LifecycleError("foreign disarm evidence")
        if args.action == "bind":
            gate.bind_fault(args.token, fault, receipt_time_ns=path.stat().st_mtime_ns)
            result = {"bound": True}
        else:
            gate.release(args.token, record["receipt"])
            result = {"released": True}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
