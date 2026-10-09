"""Provision verbatim at /opt/opencitadel-capacity/guest_bridge.py.

Standalone standard-library OS adapter for QGA (no application imports assumed).
Operator provisions a root-owned 0600 /etc/opencitadel-capacity.json containing
exact current container IDs/image IDs/argv/user/mount/network/env digests. It is
private configuration, never a success receipt. Every call inspects Docker.
"""

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from uuid import UUID

ACTIONS = {
    "cold-window",
    "status",
    "stamp",
    "client-ready",
    "client-done",
    "abort",
    "result",
    "progress",
    "infrastructure",
    "seal",
}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def inspect_owned(expected, *, running=True):
    result = subprocess.run(
        ["/usr/bin/docker", "inspect", "--type", "container", expected["id"]],
        check=True,
        capture_output=True,
        timeout=5,
    )
    if len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("container inspect exceeds bound")
    rows = json.loads(result.stdout)
    if len(rows) != 1:
        raise ValueError("ambiguous container identity")
    row = rows[0]
    actual = {
        "id": row["Id"],
        "image": row["Image"],
        "argv": [row["Path"], *row["Args"]],
        "user": row["Config"]["User"],
        "mounts": sorted(
            [
                {"source": m["Source"], "destination": m["Destination"], "rw": m["RW"]}
                for m in row["Mounts"]
            ],
            key=lambda m: m["destination"],
        ),
        "env_digest": hashlib.sha256(encoded(sorted(row["Config"]["Env"]))).hexdigest(),
        "network_ids": sorted(n["NetworkID"] for n in row["NetworkSettings"]["Networks"].values()),
    }
    if (
        actual != expected
        or (running is not None and row["State"]["Running"] is not running)
        or row["HostConfig"]["Privileged"] is not False
    ):
        raise ValueError("actual owned container identity/state differs")
    return row


def process_snapshot(pid, *, proc_root=Path("/proc")):
    """Actual process identity; two start/argv reads reject exit/reuse races."""
    root = proc_root / str(pid)
    before = (root / "stat").read_text().rsplit(")", 1)[1].split()
    argv = (root / "cmdline").read_bytes()
    # Do not resolve this magic link into the observer's mount namespace.
    # Opening procfs directly retains the actual executable inode across rename.
    executable = root / "exe"
    fd = os.open(executable, os.O_RDONLY)
    try:
        first = os.fstat(fd)
        if not stat.S_ISREG(first.st_mode):
            raise ValueError("actual executable is not a regular inode")
        hashed = hashlib.sha256()
        while chunk := os.read(fd, 1024 * 1024):
            hashed.update(chunk)
        last = os.fstat(fd)
        current = executable.stat()
        if (first.st_dev, first.st_ino, first.st_size, first.st_mtime_ns, first.st_ctime_ns) != (
            last.st_dev,
            last.st_ino,
            last.st_size,
            last.st_mtime_ns,
            last.st_ctime_ns,
        ) or (current.st_dev, current.st_ino) != (first.st_dev, first.st_ino):
            raise ValueError("process executable changed during readback")
        value = {
            "pid": pid,
            "start_ticks": int(before[19]),
            "argv_digest": hashlib.sha256(argv).hexdigest(),
            "executable_sha256": hashed.hexdigest(),
            "executable_device": first.st_dev,
            "executable_inode": first.st_ino,
            "executable_link": os.readlink(executable),
            "cgroup": (root / "cgroup").read_text().strip(),
        }
        after = (root / "stat").read_text().rsplit(")", 1)[1].split()
        current = executable.stat()
        if (
            before[19] != after[19]
            or argv != (root / "cmdline").read_bytes()
            or (current.st_dev, current.st_ino) != (first.st_dev, first.st_ino)
        ):
            raise ValueError("process changed during readback")
    finally:
        os.close(fd)
    return value


def discover_start(identity, *, proc_root=Path("/proc")):
    """Reconcile an uncertain exec without retrying; absence is NOT quiescence."""
    found = []
    entries = list(proc_root.iterdir())
    if len(entries) > 65536:
        raise ValueError("process discovery bound exceeded")
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
            argv = raw.rstrip(b"\0").split(b"\0")
            if (
                argv[:4]
                != [
                    b"/usr/bin/python3",
                    b"-I",
                    b"/opt/opencitadel-capacity/guest_bridge.py",
                    b"cold-window",
                ]
                or len(argv) != 5
            ):
                continue
            if json.loads(argv[4]) != {"identity": identity}:
                continue
            observed = process_snapshot(int(entry.name), proc_root=proc_root)
            if observed["argv_digest"] != hashlib.sha256(raw).hexdigest():
                raise ValueError("start process changed during discovery")
            found.append(observed)
        except FileNotFoundError:
            # Exited while inspecting; cannot establish absence of descendants.
            continue
    if len(found) > 1:
        raise ValueError("multiple exact guest start processes; retained")
    return found[0] if found else None


def discovery(request, containers):
    if set(request) != {"identity", "phase"} or request["phase"] not in {"discover", "reconcile"}:
        raise ValueError("fixed status discovery request required")
    identity = request["identity"]
    keys = {"attempt_id", "sample_id", "window_id", "nonce", "source_digest"}
    if request["phase"] == "reconcile":
        keys.add("boot_id")
    if set(identity) != keys:
        raise ValueError("fixed discovery identity required")
    for key in keys - {"source_digest"}:
        UUID(identity[key])
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    UUID(boot)
    if request["phase"] == "reconcile" and identity["boot_id"] != boot:
        raise ValueError("reconciliation boot differs")
    bound = {**identity, "boot_id": boot}
    return {
        "identity": bound,
        "phase": request["phase"],
        "start_process": discover_start(bound),
        "containers": [
            {"id": c["Id"], "image": c["Image"], "process": process_snapshot(c["State"]["Pid"])}
            for c in containers
        ],
        "disposition": "observation-only-retained",
    }


def response_bytes(value):
    if not isinstance(value, dict) or {"bridge_process", "bridge_sha256"} & set(value):
        raise ValueError("fixed helper response object required")
    result = encoded(
        {
            **value,
            "bridge_process": process_snapshot(os.getpid()),
            "bridge_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
    )
    if len(result) > 1024 * 1024:
        raise ValueError("wrapped helper response exceeds bound")
    return result


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ACTIONS or len(sys.argv[2].encode()) > 32768:
        raise ValueError("fixed action/request required")
    action, raw = sys.argv[1:]
    request = json.loads(raw)
    fd = os.open("/etc/opencitadel-capacity.json", os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise ValueError("private root-owned guest provision manifest required")
        manifest = json.loads(handle.read(1024 * 1024 + 1))
    if manifest["source_digest"] != request["identity"]["source_digest"]:
        raise ValueError("provisioned source differs")
    if action == "seal":
        # The fixed observer survives producer exit. Phase-aware full ownership
        # verification is performed inside its pinned implementation.
        path = Path("/opt/opencitadel-capacity/guest_seal_entry.py")
        python = Path("/opt/opencitadel-capacity/observer/bin/python")
        config = manifest["seal"]
        for target, expected in (
            (path, config["helper_sha256"]),
            (python, config["python_sha256"]),
        ):
            info = target.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or info.st_mode & 0o022
                or info.st_nlink != 1
                or hashlib.sha256(target.read_bytes()).hexdigest() != expected
            ):
                raise ValueError("fixed observer helper/runtime differs")
        if not 1 <= config["phase_timeout_seconds"] <= 3600:
            raise ValueError("fixed seal timeout invalid")
        result = subprocess.run(
            [str(python), "-I", "-B", str(path), raw],
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
            capture_output=True,
            check=True,
            timeout=config["phase_timeout_seconds"],
        )
        if result.stderr or len(result.stdout) > 1024 * 1024:
            raise ValueError("fixed seal helper output invalid")
        sys.stdout.buffer.write(response_bytes(json.loads(result.stdout)))
        return
    containers = [inspect_owned(container) for container in manifest["containers"]]
    if action == "infrastructure":
        # QGA OS adapter has no application imports. Load only the fixed pinned
        # root-owned companion, never a request-provided module or executable.
        import importlib.util

        path = Path("/opt/opencitadel-capacity/guest_infrastructure.py")
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o022
            or info.st_nlink != 1
            or hashlib.sha256(path.read_bytes()).hexdigest()
            != manifest["calibration"]["infrastructure_sha256"]
        ):
            raise ValueError("provisioned infrastructure helper identity differs")
        spec = importlib.util.spec_from_file_location("capacity_infrastructure", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        value = module.infrastructure(request, manifest, containers, process_snapshot)
        sys.stdout.buffer.write(response_bytes(value))
        return
    if action == "status" and "phase" in request:
        sys.stdout.buffer.write(response_bytes(discovery(request, containers)))
        return
    target = next(c for c in manifest["containers"] if c["id"] == manifest["driver_container_id"])
    required = {"/capacity": False, "/capacity-binding.json": False, "/capacity-live": True}
    mounts = {m["destination"]: m["rw"] for m in target["mounts"]}
    if any(mounts.get(path) is not writable for path, writable in required.items()):
        raise ValueError("fixed driver mounts not provisioned")
    timeout = 240 if action == "cold-window" else 10
    result = subprocess.run(
        [
            "/usr/bin/docker",
            "exec",
            "--user",
            target["user"],
            target["id"],
            "/app/.venv/bin/python",
            "-m",
            "scripts.execution_capacity.guest_main",
            action,
            raw,
        ],
        timeout=timeout,
        check=True,
        capture_output=action != "cold-window",
    )
    if action != "cold-window":
        if len(result.stdout) > 1024 * 1024 or result.stderr:
            raise ValueError("container helper output invalid")
        sys.stdout.buffer.write(response_bytes(json.loads(result.stdout)))


if __name__ == "__main__":
    main()
