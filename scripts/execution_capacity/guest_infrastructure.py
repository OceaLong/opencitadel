"""Standalone fixed OS resource/calibration adapter loaded by guest_bridge.

Provision beside guest_bridge.py; root manifest pins this file, the service/unit,
Python, and the exact PostgreSQL container. No arbitrary command or path input.
"""

import hashlib
import os
import re
import signal
import subprocess
import time
from pathlib import Path

UNIT = "opencitadel-capacity-calibration.service"
SERVICE = Path("/opt/opencitadel-capacity/calibration.py")
UNIT_PATH = Path("/etc/systemd/system") / UNIT
CGROUP = Path("/sys/fs/cgroup")


def observe_pg(container, *, proc=Path("/proc"), cgroup=CGROUP):
    pid = container["State"]["Pid"]
    membership = (proc / str(pid) / "cgroup").read_text().strip()
    if not membership.startswith("0::/") or "\n" in membership:
        raise ValueError("unified PostgreSQL cgroup required")
    relative = membership[4:]
    if ".." in Path(relative).parts:
        raise ValueError("invalid PostgreSQL cgroup path")
    path = cgroup / relative
    if path.resolve() != path:
        raise ValueError("PostgreSQL cgroup symlink rejected")
    ancestors = []
    for parent in (path, *path.parents):
        if parent != cgroup and cgroup not in parent.parents:
            break
        file = parent / "memory.max"
        limit = file.read_text().strip() if file.exists() or parent != cgroup else "max"
        ancestors.append({"path": str(parent), "memory.max": limit})
        if parent == cgroup:
            break
    finite = [int(row["memory.max"]) for row in ancestors if row["memory.max"] != "max"]
    value = {
        "pid": pid,
        "membership": "/" + relative,
        "container_memory_bytes": container["HostConfig"]["Memory"],
        "effective_memory_bytes": min(finite) if finite else None,
        "swap_max": int((path / "memory.swap.max").read_text()),
        "processes": [int(v) for v in (path / "cgroup.procs").read_text().split()],
        "ancestors": ancestors,
    }
    for name in ("memory.current", "memory.events", "cpu.stat"):
        value[name] = (path / name).read_text().strip()
    if (
        value["container_memory_bytes"] != 8 * 1024**3
        or value["effective_memory_bytes"] != 8 * 1024**3
        or value["swap_max"] != 0
        or pid not in value["processes"]
    ):
        raise ValueError("actual PostgreSQL memory/membership differs")
    return value


def listener_identity(pid, *, proc=Path("/proc")):
    sockets = set()
    for fd in (proc / str(pid) / "fd").iterdir():
        target = os.readlink(fd)
        match = re.fullmatch(r"socket:\[(\d+)\]", target)
        if match:
            sockets.add(int(match[1]))
    found = []
    for line in (proc / str(pid) / "net/tcp").read_text().splitlines()[1:]:
        fields = line.split()
        if fields[1] == "0F02000A:A8B7" and fields[3] == "0A":
            inode = int(fields[9])
            if inode in sockets:
                found.append({"address": "10.0.2.15", "port": 43191, "socket_inode": inode})
    if len(found) != 1:
        raise ValueError("exact owned calibration listener not observed")
    return found[0]


def systemd_state():
    result = subprocess.run(
        [
            "/usr/bin/systemctl",
            "show",
            UNIT,
            "--no-pager",
            "--property=LoadState,ActiveState,SubState,MainPID,ControlGroup,Result,NRestarts,ExecMainPID,ExecMainStartTimestampMonotonic",
        ],
        check=True,
        capture_output=True,
        timeout=5,
    )
    if len(result.stdout) > 65536:
        raise ValueError("service observation exceeds bound")
    row = dict(line.split("=", 1) for line in result.stdout.decode().splitlines())
    if row["LoadState"] != "loaded" or row["NRestarts"] != "0":
        raise ValueError("fixed service provisioning/restart state differs")
    return row


def pinned_file(path, expected):
    if path != path.resolve(strict=True):
        raise ValueError("provisioned helper symlink rejected")
    info = path.stat()
    if info.st_uid != 0 or info.st_mode & 0o022 or info.st_nlink != 1:
        raise ValueError("provisioned helper must be root owned and immutable to others")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError("provisioned helper bytes differ")
    return actual


def service_observation(manifest, snapshot):
    config = manifest["calibration"]
    pinned_file(SERVICE, config["service_sha256"])
    pinned_file(UNIT_PATH, config["unit_sha256"])
    state = systemd_state()
    if (
        state["ActiveState"] != "active"
        or state["SubState"] != "running"
        or int(state["MainPID"]) <= 0
        or state["ExecMainPID"] != state["MainPID"]
        or int(state["ExecMainStartTimestampMonotonic"]) <= 0
    ):
        raise ValueError("fixed calibration service not running")
    pid = int(state["MainPID"])
    process = snapshot(pid)
    expected = b"\0".join([b"/usr/bin/python3", b"-I", str(SERVICE).encode(), b"serve"]) + b"\0"
    if (
        process["argv_digest"] != hashlib.sha256(expected).hexdigest()
        or process["executable_sha256"] != config["python_sha256"]
        or process["cgroup"] != "0::" + state["ControlGroup"]
        or state["ControlGroup"] != "/system.slice/" + UNIT
    ):
        raise ValueError("actual calibration process identity differs")
    group = CGROUP / state["ControlGroup"].lstrip("/")
    members = [int(v) for v in (group / "cgroup.procs").read_text().split()]
    if (
        members != [pid]
        or (group / "memory.max").read_text().strip() != "268435456"
        or (group / "memory.swap.max").read_text().strip() != "0"
        or (lambda parts: parts[0] == "max" or int(parts[0]) != int(parts[1]))(
            (group / "cpu.max").read_text().split()
        )
    ):
        raise ValueError("calibration resource membership/allocation differs")
    deadline = time.monotonic() + 2
    while True:
        try:
            endpoint = listener_identity(pid)
            break
        except ValueError:
            if time.monotonic() >= deadline or snapshot(pid) != process:
                raise
            time.sleep(0.01)
    if snapshot(pid) != process:
        raise ValueError("calibration process changed during observation")
    return {
        "process": process,
        "endpoint": endpoint,
        "state": state,
        "server": {
            "pid": pid,
            "start_ticks": process["start_ticks"],
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "service_sha256": config["service_sha256"],
        },
        "resources": {
            name: (group / name).read_text().strip()
            for name in ("memory.current", "memory.events", "cpu.stat", "cpu.max", "cgroup.events")
        },
    }


def infrastructure(request, manifest, containers, snapshot):
    if request.get("phase") not in {"resources", "start", "observe", "stop"} or set(request) != (
        {"identity", "phase", "server"} if request["phase"] == "stop" else {"identity", "phase"}
    ):
        raise ValueError("fixed infrastructure request required")
    identity, phase = request["identity"], request["phase"]
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if identity["boot_id"] != boot:
        raise ValueError("infrastructure boot differs")
    config = manifest["calibration"]
    pinned_file(Path(__file__), config["infrastructure_sha256"])
    result = {
        "identity": identity,
        "phase": phase,
        "guest_ns": time.monotonic_ns(),
        "infrastructure_sha256": config["infrastructure_sha256"],
    }
    if phase == "resources":
        pg = next(c for c in containers if c["Id"] == manifest["postgres_container_id"])
        result.update(
            postgres=observe_pg(pg),
            postgres_process=snapshot(pg["State"]["Pid"]),
            online_cpus=Path("/sys/devices/system/cpu/online").read_text().strip(),
            meminfo=Path("/proc/meminfo").read_text(),
            cpu_stat=Path("/proc/stat").read_text(),
            architecture=os.uname().machine,
        )
        return result
    pinned_file(SERVICE, config["service_sha256"])
    pinned_file(UNIT_PATH, config["unit_sha256"])
    if phase == "start":
        before = systemd_state()
        if before["ActiveState"] != "inactive" or before["MainPID"] != "0":
            raise ValueError("calibration service not fresh inactive")
        subprocess.run(
            ["/usr/bin/systemctl", "start", UNIT], check=True, capture_output=True, timeout=5
        )
    if phase in {"start", "observe"}:
        result["calibration"] = service_observation(manifest, snapshot)
        return result
    before = service_observation(manifest, snapshot)
    if before["server"] != request["server"]:
        raise ValueError("calibration process differs from host-owned stop identity")
    fd = os.pidfd_open(before["process"]["pid"])
    try:
        if snapshot(before["process"]["pid"]) != before["process"]:
            raise ValueError("service changed before stop")
        # A mutable unit name is never termination authority. Only the retained,
        # reauthenticated pidfd can receive the signal, even if P exits and Q
        # replaces it between the last identity check and this syscall.
        try:
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            signal_result = "sent"
        except ProcessLookupError:
            signal_result = "original-process-already-exited"
        import select

        poll = select.poll()
        poll.register(fd, select.POLLIN)
        exited = any(
            event_fd == fd and events & select.POLLIN for event_fd, events in poll.poll(5000)
        )
        if not exited:
            raise ValueError("calibration service exit not proved; retained")
        # These are supporting observations only; no stop/kill/reset-failed
        # operation is issued against the systemd unit or its cgroup.
        after = systemd_state()
        group = CGROUP / before["state"]["ControlGroup"].lstrip("/")
        if (
            after["MainPID"] != "0"
            or after["ExecMainPID"] != before["state"]["ExecMainPID"]
            or after["ExecMainStartTimestampMonotonic"]
            != before["state"]["ExecMainStartTimestampMonotonic"]
            or after["ActiveState"] != "inactive"
            or (group.exists() and "populated 0" not in (group / "cgroup.events").read_text())
        ):
            raise ValueError("calibration service exit not proved; retained")
        result.update(
            before=before,
            after=after,
            signal_result=signal_result,
            disposition="service-exit-observed",
        )
        return result
    finally:
        os.close(fd)
