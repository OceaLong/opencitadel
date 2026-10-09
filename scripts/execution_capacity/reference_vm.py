"""Fixed Linux KVM cold VM operations and independent process/disk readbacks.

These methods have real effects ONLY when explicitly called by the coordinator.
No fallback acceleration, arbitrary command hook, cache flush or overlay deletion.
"""

import json
import os
import select
import signal
import stat
import subprocess
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from scripts.execution_capacity.attempt import host_clock
from scripts.execution_capacity.guest_bridge import process_snapshot
from scripts.execution_capacity.reference_protocol import ProtocolError, connect_owned


def file_identity(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("private regular single-link file required")
        hashed = sha256()
        while chunk := os.read(fd, 1024 * 1024):
            hashed.update(chunk)
        after = os.fstat(fd)
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("file changed while verifying")
        return {
            "device": info.st_dev,
            "inode": info.st_ino,
            "size_bytes": info.st_size,
            "sha256": hashed.hexdigest(),
        }
    finally:
        os.close(fd)


def process_identity(pid):
    return {**process_snapshot(pid), "boot_id": host_clock()["boot_id"]}


def argv_digest(argv):
    """Same exact NUL-delimited bytes as /proc/PID/cmdline."""
    return sha256(b"\0".join(os.fsencode(v) for v in argv) + b"\0").hexdigest()


@dataclass(frozen=True)
class VMPlan:
    uuid: str
    machine: str
    qemu_sha256: str
    qemu_img_sha256: str
    firmware: Path
    firmware_sha256: str
    base: Path
    base_identity: dict
    overlay: Path
    qmp_socket: Path
    qga_socket: Path
    host_address: str
    ports: tuple[int, ...]
    cpu_ids: tuple[int, ...]

    def validate(self):
        import ipaddress
        import re

        UUID(self.uuid)
        if not re.fullmatch(r"pc-q35-[0-9]+\.[0-9]+", self.machine):
            raise ValueError("pinned q35 machine version required")
        if (
            len(self.cpu_ids) != 16
            or len(set(self.cpu_ids)) != 16
            or any(type(c) is not int or c < 0 for c in self.cpu_ids)
        ):
            raise ValueError("16 exact server CPUs required")
        if not ipaddress.IPv4Address(self.host_address).is_private:
            raise ValueError("owned private host-veth address required")
        if (
            not self.ports
            or len(set(self.ports)) != len(self.ports)
            or any(type(p) is not int or not 1024 <= p <= 65535 for p in self.ports)
        ):
            raise ValueError("exact unprivileged forwarded ports required")
        for path in (self.base, self.overlay, self.qmp_socket, self.qga_socket, self.firmware):
            if not path.is_absolute() or path != path.resolve() or "," in str(path):
                raise ValueError("absolute non-symlink QEMU paths required")

    def argv(self):
        self.validate()
        nodes = [
            {
                "driver": "file",
                "node-name": "base-file",
                "filename": str(self.base),
                "read-only": True,
            },
            {"driver": "raw", "node-name": "base", "file": "base-file", "read-only": True},
            {"driver": "file", "node-name": "round-file", "filename": str(self.overlay)},
            {"driver": "qcow2", "node-name": "round-root", "file": "round-file", "backing": "base"},
        ]
        args = [
            "/usr/bin/qemu-system-x86_64",
            "-S",
            "-machine",
            self.machine + ",accel=kvm",
            "-cpu",
            "host",
            "-smp",
            "16",
            "-m",
            "32768",
            "-uuid",
            self.uuid,
            "-bios",
            str(self.firmware),
            "-nodefaults",
            "-no-user-config",
            "-display",
            "none",
            "-monitor",
            "none",
            "-qmp",
            f"unix:{self.qmp_socket},server=on,wait=off",
            "-chardev",
            f"socket,path={self.qga_socket},server=on,wait=off,id=qga",
            "-device",
            "virtio-serial-pci",
            "-device",
            "virtserialport,chardev=qga,name=org.qemu.guest_agent.0",
        ]
        for node in nodes:
            node["cache"] = {"direct": True, "no-flush": False}
            args += ["-blockdev", json.dumps(node, separators=(",", ":"))]
        net = "user,id=owned,restrict=on" + "".join(
            f",hostfwd=tcp:{self.host_address}:{p}-:{p}" for p in self.ports
        )
        return [
            *args,
            "-device",
            "virtio-blk-pci,drive=round-root,serial=" + self.uuid.replace("-", "")[:20],
            "-netdev",
            net,
            "-device",
            "virtio-net-pci,netdev=owned",
        ]


def vm_plan_record(plan):
    return {
        k: str(v) if isinstance(v, Path) else list(v) if isinstance(v, tuple) else v
        for k, v in asdict(plan).items()
    }


def verify_nodes(rows, plan, physical_fds):
    """Parse real query-named-block-nodes graph (requires actual children)."""
    expected = {
        "base-file": ("file", None, None, True),
        "base": ("raw", "base-file", None, True),
        "round-file": ("file", None, None, False),
        "round-root": ("qcow2", "round-file", "base", False),
    }
    actual = {r["node-name"]: r for r in rows}
    if len(actual) != len(rows) or set(actual) != set(expected):
        raise ProtocolError("unexpected/missing block graph nodes")
    result = []
    for name, (driver, child, backing, readonly) in expected.items():
        row = actual[name]
        children = {r["name"]: r["info"]["node-name"] for r in row["children"]}
        if (
            row["drv"] != driver
            or row["ro"] is not readonly
            or row["cache"] != {"direct": True, "no-flush": False, "writeback": True}
        ):
            raise ProtocolError("block driver/readonly/cache differs")
        if children != {k: v for k, v in [("file", child), ("backing", backing)] if v is not None}:
            raise ProtocolError("block child graph differs")
        entry = {
            "node_name": name,
            "driver": driver,
            "child": child,
            "backing": backing,
            "cache_direct": True,
            "cache_no_flush": False,
            "read_only": readonly,
            "device": None,
            "inode": None,
            "fd_direct": None,
            "image_id": None,
        }
        if driver == "file":
            path = plan.base if name == "base-file" else plan.overlay
            info = path.stat()
            if row["file"] != str(path) or (info.st_dev, info.st_ino) not in physical_fds:
                raise ProtocolError("physical file descriptor identity differs")
            entry.update(device=info.st_dev, inode=info.st_ino, fd_direct=True)
        result.append(entry)
    return result


def direct_fds(pid, paths):
    wanted = {(p.stat().st_dev, p.stat().st_ino) for p in paths}
    observed = set()
    for path in (Path("/proc") / str(pid) / "fd").iterdir():
        try:
            info = path.stat()
        except FileNotFoundError:
            continue
        identity = (info.st_dev, info.st_ino)
        if identity in wanted:
            values = dict(
                line.split(":", 1)
                for line in (path.parent.parent / "fdinfo" / path.name).read_text().splitlines()
            )
            if not int(values["flags"].strip(), 8) & os.O_DIRECT:
                raise ProtocolError("backing/overlay FD lacks O_DIRECT")
            observed.add(identity)
    if observed != wanted:
        raise ProtocolError("missing actual backing/overlay descriptor")
    return observed


def socket_identity(path):
    from scripts.execution_capacity.ownership import _private_directory

    _private_directory(path.parent)
    observed = path.lstat()
    if not stat.S_ISSOCK(observed.st_mode) or observed.st_uid != os.getuid():
        raise ValueError("owned Unix socket required")
    return {"device": observed.st_dev, "inode": observed.st_ino, "uid": observed.st_uid}


class ColdVM:
    def __init__(self, plan, ledger, *, sample_id, window_id):
        self.plan, self.ledger = plan, ledger
        self.sample_id, self.window_id = sample_id, window_id
        self.overlay_verified = False
        self.process = self.pidfd = self.identity = None

    def accept_resource_transition(self, allocation):
        with self.ledger.control_lock:
            if not any(r["body"] == allocation for r in self.ledger.records("resource-process")):
                raise ValueError("actual resource observation required")
            current = process_identity(self.process.pid)
            expected = {**allocation["identity"], "boot_id": self.identity["boot_id"]}
            if current != expected or {k: v for k, v in current.items() if k != "cgroup"} != {
                k: v for k, v in self.identity.items() if k != "cgroup"
            }:
                raise ValueError("process identity changed during resource transition")
            self.ledger.append(
                "qemu-resource-transition",
                {
                    "uuid": self.plan.uuid,
                    "before": self.identity,
                    "after": current,
                    "host_ns": time.monotonic_ns(),
                },
            )
            self.identity = current

    def open_channel(self, kind, *, timeout=2):
        if kind not in {"qmp", "qga"} or self.pidfd is None or self.identity is None:
            raise ValueError("exact owned VM and fixed socket kind required")
        if process_identity(self.process.pid) != self.identity:
            raise ValueError("VM identity changed before socket discovery")
        path = getattr(self.plan, kind + "_socket")
        deadline = time.monotonic() + timeout
        while True:
            try:
                observed = socket_identity(path)
                break
            except FileNotFoundError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("owned VM socket discovery timeout") from None
                time.sleep(0.01)
        previous = [
            r["body"]
            for r in self.ledger.records("qemu-socket-observed")
            if r["body"]["uuid"] == self.plan.uuid and r["body"]["kind"] == kind
        ]
        if previous and any(r["identity"] != observed for r in previous):
            raise ValueError("owned VM socket replaced")
        self.ledger.append(
            "qemu-socket-observed",
            {
                "uuid": self.plan.uuid,
                "kind": kind,
                "path": str(path),
                "identity": observed,
                "process": self.identity,
                "host_ns": time.monotonic_ns(),
            },
        )
        channel = connect_owned(path, pid=self.process.pid, **observed, timeout=timeout)
        try:
            if process_identity(self.process.pid) != self.identity:
                raise ValueError("VM identity changed during socket authentication")
        except BaseException:
            channel.wire.close()
            raise
        return channel

    def create_overlay(self):
        self.ledger.bind_clock()
        p = self.plan
        p.validate()
        self.ledger.reserve(self.sample_id, self.window_id, seal_digest=p.base_identity["sha256"])
        from scripts.execution_capacity.ownership import _private_directory

        for parent in {p.overlay.parent, p.qmp_socket.parent, p.qga_socket.parent}:
            _private_directory(parent)
        if file_identity(p.base) != p.base_identity:
            raise ValueError("sealed base changed")
        for binary, expected in [
            (Path("/usr/bin/qemu-system-x86_64"), p.qemu_sha256),
            (Path("/usr/bin/qemu-img"), p.qemu_img_sha256),
            (p.firmware, p.firmware_sha256),
        ]:
            if file_identity(binary)["sha256"] != expected:
                raise ValueError("pinned executable/firmware differs")
        if p.overlay.exists() or p.qmp_socket.exists() or p.qga_socket.exists():
            raise ValueError("fresh overlay/socket names already occupied")
        self.ledger.append(
            "overlay-create-intent",
            {"uuid": p.uuid, "base": str(p.base), "overlay": str(p.overlay)},
        )
        subprocess.run(
            [
                "/usr/bin/qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                "raw",
                "-b",
                str(p.base),
                str(p.overlay),
            ],
            check=True,
            timeout=30,
            capture_output=True,
        )
        os.chmod(p.overlay, 0o600)
        output = subprocess.run(
            ["/usr/bin/qemu-img", "info", "--output=json", "--backing-chain", str(p.overlay)],
            check=True,
            timeout=30,
            capture_output=True,
        )
        rows = json.loads(output.stdout)
        if (
            len(rows) != 2
            or rows[0]["format"] != "qcow2"
            or rows[1]["format"] != "raw"
            or rows[0]["full-backing-filename"] != str(p.base)
            or rows[1]["filename"] != str(p.base)
            or rows[0]["virtual-size"] != rows[1]["virtual-size"]
        ):
            raise ValueError("offline overlay chain differs")
        self.ledger.append(
            "overlay-created", {"uuid": p.uuid, "identity": file_identity(p.overlay), "chain": rows}
        )
        self.overlay_verified = True

    def launch(self):
        if not self.overlay_verified:
            raise ValueError("verified fresh overlay required before launch")
        if file_identity(self.plan.base) != self.plan.base_identity:
            raise ValueError("sealed base changed before launch")
        if self.process is not None:
            raise ValueError("QEMU launch already consumed")
        argv = self.plan.argv()
        # taskset is fixed and execs QEMU; affinity is re-read before QMP cont.
        command = ["/usr/bin/taskset", "--cpu-list", ",".join(map(str, self.plan.cpu_ids)), *argv]
        self.ledger.append(
            "qemu-launch-intent",
            {"uuid": self.plan.uuid, "argv": command, "host_ns": time.monotonic_ns()},
        )
        logfd = os.open(
            self.ledger.root / (self.plan.uuid + ".qemu.log"),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=logfd,
                stderr=logfd,
                close_fds=True,
                start_new_session=True,
            )
        finally:
            os.close(logfd)
        self.pidfd = os.pidfd_open(self.process.pid)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            identity = process_identity(self.process.pid)
            if identity["executable_sha256"] == self.plan.qemu_sha256:
                if identity["argv_digest"] != argv_digest(argv) or os.sched_getaffinity(
                    self.process.pid
                ) != set(self.plan.cpu_ids):
                    raise ProtocolError("actual QEMU argv/affinity differs")
                self.identity = identity
                self.ledger.append("qemu-process", identity)
                return identity
            if self.process.poll() is not None:
                break
            time.sleep(0.01)
        raise ProtocolError("fixed QEMU exec not observed; retained")

    def verify_paused(self, qmp):
        if process_identity(self.process.pid) != self.identity:
            raise ProtocolError("process identity changed")
        version = qmp.command("query-version")
        schema = qmp.command("query-qmp-schema")
        commands = {r["name"] for r in schema if r["meta-type"] == "command"}
        required = {
            "query-status",
            "query-kvm",
            "query-uuid",
            "query-cpus-fast",
            "query-memory-size-summary",
            "query-block",
            "query-named-block-nodes",
            "query-blockstats",
            "cont",
            "quit",
        }
        if (
            not required <= commands
            or qmp.command("query-status")["status"] != "prelaunch"
            or qmp.command("query-kvm") != {"enabled": True, "present": True}
            or qmp.command("query-uuid")["UUID"] != self.plan.uuid
        ):
            raise ProtocolError("paused KVM/UUID/capability proof differs")
        cpus, memory = qmp.command("query-cpus-fast"), qmp.command("query-memory-size-summary")
        if (
            len(cpus) != 16
            or memory["base-memory"] != 32 * 1024**3
            or memory.get("plugged-memory", 0) != 0
        ):
            raise ProtocolError("actual guest CPU/memory differs")
        blocks = qmp.command("query-block")
        if len(blocks) != 1 or blocks[0]["inserted"]["node-name"] != "round-root":
            raise ProtocolError("attached root disk differs")
        nodes = verify_nodes(
            qmp.command("query-named-block-nodes", {"flat": False}),
            self.plan,
            direct_fds(self.process.pid, [self.plan.base, self.plan.overlay]),
        )
        evidence = {
            "version": version,
            "commands": sorted(commands),
            "cpus": cpus,
            "memory": memory,
            "nodes": nodes,
            "blockstats": qmp.command("query-blockstats"),
        }
        self.ledger.append("qmp-paused-verified", {"uuid": self.plan.uuid, **evidence})
        return evidence

    def resume(self, qmp):
        if process_identity(self.process.pid) != self.identity or not any(
            r["body"]["uuid"] == self.plan.uuid for r in self.ledger.records("resource-vm-bound")
        ):
            raise ValueError("actual resource-bound VM required before resume")
        if any(
            r["body"]["uuid"] == self.plan.uuid for r in self.ledger.records("qemu-cont-intent")
        ):
            raise ValueError("VM resume already consumed")
        self.ledger.append("qemu-cont-intent", {"uuid": self.plan.uuid})
        qmp.command("cont")
        if qmp.command("query-status")["status"] != "running":
            raise ProtocolError("QEMU failed running readback")

    def force_exit(self):
        """Failed recovery only, using the exact retained descriptor authority."""
        with self.ledger.control_lock:
            if any(
                r["body"]["uuid"] == self.plan.uuid
                for r in self.ledger.records("qemu-force-exit-intent")
            ):
                raise ValueError("VM failure stop consumed; uncertain outcome retained")
            if (
                self.pidfd is None
                or self.identity is None
                or process_identity(self.process.pid) != self.identity
            ):
                raise ValueError("exact live owned VM identity required for failure stop")
            self.ledger.append(
                "qemu-force-exit-intent",
                {
                    "uuid": self.plan.uuid,
                    "identity": self.identity,
                    "host_ns": time.monotonic_ns(),
                    "overlay": str(self.plan.overlay),
                    "clean": False,
                },
            )
            # Descriptor exit still must be observed, even if signal returns ESRCH.
            with suppress(ProcessLookupError):
                signal.pidfd_send_signal(self.pidfd, signal.SIGTERM)
        code = self.wait_exit(5)
        self.ledger.append(
            "qemu-forced-exit",
            {
                "uuid": self.plan.uuid,
                "returncode": code,
                "host_ns": time.monotonic_ns(),
                "clean": False,
                "overlay": "retained",
                "disposition": "failed-recovery",
            },
        )
        return code

    def wait_exit(self, seconds):
        if self.pidfd is None or not 0 < seconds <= 60:
            raise ValueError("owned pidfd and bounded wait required")
        poll = select.poll()
        poll.register(self.pidfd, select.POLLIN)
        if not poll.poll(int(seconds * 1000)):
            raise TimeoutError("owned QEMU remains running; retained overlay")
        code = self.process.wait(timeout=1)
        self.ledger.append(
            "qemu-exited",
            {
                "identity": self.identity,
                "returncode": code,
                "host_ns": time.monotonic_ns(),
                "overlay": "retained",
            },
        )
        os.close(self.pidfd)
        self.pidfd = None
        return code
