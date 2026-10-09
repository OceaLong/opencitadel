"""Linux resource prerequisites, delegated allocation and actual readbacks.

Same-host x86/KVM, cgroup v2 and directly attached host NVMe on ext4/XFS are
this backend's supported subset. Other SSDs may meet the spec but need a backend.
No probe or allocation occurs at import. No cgroup creation/deletion/global edits.
"""

import fcntl
import json
import os
import platform
import re
import stat
import subprocess
import time
from pathlib import Path

from scripts.execution_capacity.guest_bridge import process_snapshot
from scripts.execution_capacity.reference_vm import direct_fds, process_identity

GIB = 1024**3
CGROUP = Path("/sys/fs/cgroup")


def cpus(raw):
    result = set()
    for item in raw.strip().split(","):
        if not item:
            continue
        if re.fullmatch(r"\d+(-\d+)?", item) is None:
            raise ValueError("invalid CPU list")
        ends = item.split("-")
        a, b = int(ends[0]), int(ends[-1])
        if b < a or b > 65535:
            raise ValueError("CPU range exceeds bound")
        result.update(range(a, b + 1))
    return sorted(result)


def identity(path):
    if path != path.resolve(strict=True):
        raise ValueError("symlink resource path rejected")
    info = path.stat()
    return {"device": info.st_dev, "inode": info.st_ino}


def cgroup_snapshot(path, *, mount=None):
    path, mount = Path(path), Path(CGROUP if mount is None else mount)
    path.relative_to(mount)
    inode = identity(path)
    ancestors = []
    for parent in (path, *path.parents):
        if parent != mount and mount not in parent.parents:
            break
        # Root cgroup has no controller limit files. Every non-root must have them.
        root = parent == mount
        values = {}
        for key in ("cpu.max", "memory.max", "memory.swap.max"):
            file = parent / key
            values[key] = file.read_text().strip() if file.exists() or not root else "max"
        ancestors.append({"path": str(parent), **values})
        if root:
            break
    result = {
        "path": str(path),
        "identity": inode,
        "ancestors": ancestors,
        "cpus": cpus((path / "cpuset.cpus.effective").read_text()),
        "mems": (path / "cpuset.mems.effective").read_text().strip(),
        "processes": [int(v) for v in (path / "cgroup.procs").read_text().split()],
        "host_ns": time.monotonic_ns(),
    }
    for key in ("memory.current", "memory.events", "cpu.stat", "cgroup.events"):
        result[key] = (path / key).read_text().strip()
    if identity(path) != inode:
        raise ValueError("cgroup changed during observation")
    return result


def validate_envelope(observed, cpu_ids, memory_bytes):
    if observed["cpus"] != sorted(cpu_ids) or not observed["mems"]:
        raise ValueError("effective cpuset differs")
    for row in observed["ancestors"]:
        quota = row["cpu.max"].split()
        if quota[0] != "max" and int(quota[0]) / int(quota[1]) < len(cpu_ids):
            raise ValueError("ancestor CPU quota reduces allocation")
        if row["memory.max"] != "max" and int(row["memory.max"]) < memory_bytes:
            raise ValueError("ancestor memory limit reduces allocation")
    leaf = observed["ancestors"][0]
    if leaf["memory.max"] != str(memory_bytes) or leaf["memory.swap.max"] != "0":
        raise ValueError("actual memory/swap envelope differs")


def validate_capacity(observed, server_cpus, client_cpus, qemu_overhead_bytes, host_headroom_bytes):
    server, client, online = set(server_cpus), set(client_cpus), set(observed["online_cpus"])
    if (
        len(server) != 16
        or len(client) < 4
        or len(server) != len(server_cpus)
        or len(client) != len(client_cpus)
        or server & client
        or not (server | client) < online
    ):
        raise ValueError("disjoint server/client CPUs and host CPU headroom required")
    topology = observed["topology"]
    server_cores = {tuple(topology[str(cpu)]) for cpu in server}
    client_cores = {tuple(topology[str(cpu)]) for cpu in client}
    online_cores = {tuple(topology[str(cpu)]) for cpu in online}
    if (
        len(server_cores) != 16
        or len(client_cores) < 4
        or server_cores & client_cores
        or not (server_cores | client_cores) < online_cores
    ):
        raise ValueError("disjoint physical core allocations and host core headroom required")
    if (
        type(qemu_overhead_bytes) is not int
        or qemu_overhead_bytes <= 0
        or type(host_headroom_bytes) is not int
        or host_headroom_bytes <= 0
        or min(observed["memory_bytes"], observed["memory_available_bytes"])
        < 48 * GIB + qemu_overhead_bytes + host_headroom_bytes
    ):
        raise ValueError("actual host memory lacks declared overhead/headroom")
    if observed["architecture"] != "x86_64" or observed["kvm_api"] != 12:
        raise ValueError("native x86_64 KVM prerequisite unavailable")


def host_capacity():
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("native Linux x86_64 backend required")
    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    fd = os.open("/dev/kvm", os.O_RDWR | os.O_CLOEXEC)
    try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode):
            raise ValueError("actual KVM character device required")
        api = fcntl.ioctl(fd, 0xAE00)  # KVM_GET_API_VERSION, read-only capability query
    finally:
        os.close(fd)
    cpu_ids = cpus(Path("/sys/devices/system/cpu/online").read_text())
    topology = {
        str(cpu): [
            int(Path(f"/sys/devices/system/cpu/cpu{cpu}/topology/{name}").read_text())
            for name in ("physical_package_id", "core_id")
        ]
        for cpu in cpu_ids
    }
    return {
        "architecture": platform.machine(),
        "topology": topology,
        "dmi": {
            name: Path("/sys/class/dmi/id", name).read_text().strip()
            for name in ("sys_vendor", "product_name", "product_version")
        },
        "cpu_siblings": {
            str(cpu): Path(f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list")
            .read_text()
            .strip()
            for cpu in cpu_ids
        },
        "kernel": platform.release(),
        "kvm_api": api,
        "online_cpus": cpus(Path("/sys/devices/system/cpu/online").read_text()),
        "memory_bytes": int(memory["MemTotal"].split()[0]) * 1024,
        "memory_available_bytes": int(memory["MemAvailable"].split()[0]) * 1024,
        "swap_total_bytes": int(memory["SwapTotal"].split()[0]) * 1024,
        "cpuinfo": Path("/proc/cpuinfo").read_text(),
        "pressure": {
            name: Path("/proc/pressure", name).read_text() for name in ("cpu", "memory", "io")
        },
        "cpu_stat": Path("/proc/stat").read_text(),
        "host_ns": time.monotonic_ns(),
    }


def storage_backing(path):
    """Resolve host file st_dev to actual PCI NVMe block device, never guest virtio."""
    info = path.stat()
    dev = f"{os.major(info.st_dev)}:{os.minor(info.st_dev)}"
    block = (Path("/sys/dev/block") / dev).resolve(strict=True)
    if (block / "partition").exists():
        block = block.parent
    if "/virtual/" in str(block) or not str(block).startswith("/sys/devices/pci"):
        raise ValueError("backend requires physical host PCI NVMe backing")
    controller = block.parent
    if controller.joinpath("subsystem").resolve().name != "nvme":
        raise ValueError("storage backend does not support this physical device")
    if (block / "queue/rotational").read_text().strip() != "0" or list(
        (block / "slaves").iterdir()
    ):
        raise ValueError("non-direct NVMe backing unsupported")
    out = subprocess.run(
        [
            "/usr/bin/findmnt",
            "--json",
            "--target",
            str(path),
            "--output",
            "TARGET,SOURCE,FSTYPE,OPTIONS,MAJ:MIN",
        ],
        check=True,
        capture_output=True,
        timeout=5,
    )
    rows = json.loads(out.stdout)["filesystems"]
    if len(rows) != 1 or rows[0]["fstype"] not in {"ext4", "xfs"} or rows[0]["maj:min"] != dev:
        raise ValueError("actual host ext4/XFS device mapping differs")
    return {
        "file_device": dev,
        "file_inode": info.st_ino,
        "sysfs_block": str(block),
        "block_device": (block / "dev").read_text().strip(),
        "mount": rows[0],
        "model": (controller / "model").read_text().strip(),
        "serial": (controller / "serial").read_text().strip(),
        "firmware": (controller / "firmware_rev").read_text().strip(),
        "namespace_id": (block / "nsid").read_text().strip(),
        "device_caches": "uncontrolled",
    }


def validate_pg(observed):
    if (
        observed["container_memory_bytes"] != 8 * GIB
        or observed["effective_memory_bytes"] != 8 * GIB
        or observed["swap_max"] != 0
        or observed["pid"] not in observed["processes"]
        or not observed["membership"].startswith("/")
    ):
        raise ValueError("actual PostgreSQL cgroup allocation/membership differs")


class ResourceAllocation:
    """Only exact predelegated isolated cgroup leaves from private immutable plan."""

    def __init__(self, ledger):
        self.ledger = ledger
        self.plan = ledger.plan["resources"]
        if self.plan["topology"] != "same-host-linux-x86-kvm":
            raise ValueError("separate-host backend not implemented")
        if set(self.plan["groups"]) != {"server", "client"}:
            raise ValueError("exact server/client groups required")
        paths = [Path(v["path"]) for v in self.plan["groups"].values()]
        if paths[0] == paths[1] or paths[0].parent != paths[1].parent:
            raise ValueError("distinct sibling delegated groups required")
        for path in paths:
            if not path.is_relative_to(CGROUP) or path == CGROUP:
                raise ValueError("delegated cgroup-v2 leaf required")

    def _group(self, role):
        row = self.plan["groups"][role]
        path = Path(row["path"])
        if identity(path) != row["identity"]:
            raise ValueError("delegated cgroup identity differs")
        if (path / "cpuset.cpus.partition").read_text().strip() != "isolated":
            raise ValueError("predelegated isolated CPU partition required")
        return path, row

    def _memory(self, role):
        return 32 * GIB + self.plan["qemu_overhead_bytes"] if role == "server" else 16 * GIB

    def configure(self):
        with self.ledger.control_lock:
            if self.ledger.count("resource-configure-intent"):
                raise ValueError("resource allocation already consumed; inspect retained state")
            observed = host_capacity()
            groups = self.plan["groups"]
            validate_capacity(
                observed,
                groups["server"]["cpus"],
                groups["client"]["cpus"],
                self.plan["qemu_overhead_bytes"],
                self.plan["host_headroom_bytes"],
            )
            for role in groups:
                path, expected = self._group(role)
                if (path / "cgroup.procs").read_text().strip() or any(
                    p.is_dir() for p in path.iterdir()
                ):
                    raise ValueError("delegated group must be empty leaf before allocation")
                if cpus((path / "cpuset.cpus.effective").read_text()) != sorted(expected["cpus"]):
                    raise ValueError("predelegated CPU partition allocation differs")
            self.ledger.append(
                "resource-configure-intent", {"plan": self.plan, "hardware": observed}
            )
            for role in groups:
                path, _ = self._group(role)
                for key, value in {
                    "memory.max": str(self._memory(role)),
                    "memory.min": str(self._memory(role)),
                    "memory.swap.max": "0",
                    "cpu.max": "max 100000",
                }.items():
                    (path / key).write_text(value)
            return self.observe()

    def observe(self):
        groups = {}
        for role in self.plan["groups"]:
            path, row = self._group(role)
            observed = cgroup_snapshot(path)
            validate_envelope(observed, row["cpus"], self._memory(role))
            observed["memory.min"] = (path / "memory.min").read_text().strip()
            observed["cpuset.cpus.partition"] = (path / "cpuset.cpus.partition").read_text().strip()
            if observed["memory.min"] != str(self._memory(role)):
                raise ValueError("memory protection allocation differs")
            groups[role] = observed
        parent = Path(self.plan["groups"]["server"]["path"]).parent
        total_memory = sum(self._memory(role) for role in groups)
        total_cpus = sum(len(self.plan["groups"][role]["cpus"]) for role in groups)
        for ancestor in (parent, *parent.parents):
            if ancestor == CGROUP:
                break
            if int((ancestor / "memory.min").read_text()) < total_memory:
                raise ValueError("delegated ancestor memory protection insufficient")
            maximum = (ancestor / "memory.max").read_text().strip()
            quota, period = (ancestor / "cpu.max").read_text().split()
            if maximum != "max" and int(maximum) < total_memory:
                raise ValueError("combined server/client ancestor memory insufficient")
            if quota != "max" and int(quota) / int(period) < total_cpus:
                raise ValueError("combined server/client ancestor CPU quota insufficient")
        self.ledger.append(
            "resource-observation", {"groups": groups, "host_ns": time.monotonic_ns()}
        )
        return groups

    def bind(self, role, pid, expected):
        """Move only independently owned process. Called for paused QEMU or self-launcher.

        Already running native work must never be measured before bind returns.
        Any partial move/affinity response is retained, not retried automatically.
        """
        with self.ledger.control_lock:
            self.observe()
            if process_snapshot(pid) != expected:
                raise ValueError("resource consumer process identity differs")
            if any(
                all(
                    r["body"]["identity"][key] == expected[key]
                    for key in ("pid", "start_ticks", "argv_digest", "executable_sha256")
                )
                for r in self.ledger.records("resource-bind-intent")
            ):
                raise ValueError("resource bind consumed; read back retained membership")
            path, row = self._group(role)
            self.ledger.append("resource-bind-intent", {"role": role, "identity": expected})
            (path / "cgroup.procs").write_text(str(pid))
            for task in Path("/proc", str(pid), "task").iterdir():
                os.sched_setaffinity(int(task.name), row["cpus"])
            return self.observe_process(role, pid, expected)

    def observe_process(self, role, pid, expected):
        path, row = self._group(role)
        current = process_snapshot(pid)
        # Moving into an owned cgroup intentionally changes the membership field.
        if {k: v for k, v in current.items() if k != "cgroup"} != {
            k: v for k, v in expected.items() if k != "cgroup"
        }:
            raise ValueError("process changed during resource observation")
        wanted = "0::/" + str(path.relative_to(CGROUP))
        if current["cgroup"] != wanted:
            raise ValueError("actual running process cgroup differs")
        snapshot = cgroup_snapshot(path)
        if pid not in snapshot["processes"]:
            raise ValueError("process absent from owned cgroup")
        threads = {}
        for task in Path("/proc", str(pid), "task").iterdir():
            affinity = sorted(os.sched_getaffinity(int(task.name)))
            if affinity != sorted(row["cpus"]):
                raise ValueError("actual thread affinity differs")
            threads[task.name] = {"affinity": affinity, "stat": (task / "stat").read_text()}
        observed = {
            "role": role,
            "identity": current,
            "cgroup": snapshot,
            "threads": threads,
            "process_status": Path("/proc", str(pid), "status").read_text(),
            "io": Path("/proc", str(pid), "io").read_text(),
        }
        self.ledger.append("resource-process", observed)
        return observed

    def bind_vm(self, vm, network):
        if tuple(vm.plan.cpu_ids) != tuple(self.plan["groups"]["server"]["cpus"]):
            raise ValueError("VM CPU plan differs from allocation")
        if vm.plan.host_address != network.plan.host_address or vm.identity is None:
            raise ValueError("observed VM/network plan binding required")
        network.reconcile()
        if (
            process_identity(vm.process.pid) != vm.identity
            or not any(
                r["body"]["uuid"] == vm.plan.uuid
                for r in self.ledger.records("qmp-paused-verified")
            )
            or any(
                r["body"]["uuid"] == vm.plan.uuid for r in self.ledger.records("qemu-cont-intent")
            )
        ):
            raise ValueError("independently verified paused owned VM required")
        process = process_snapshot(vm.process.pid)
        allocation = self.bind("server", vm.process.pid, process)
        vm.accept_resource_transition(allocation)
        disks = [storage_backing(path) for path in (vm.plan.base, vm.plan.overlay)]
        fds = sorted(direct_fds(vm.process.pid, [vm.plan.base, vm.plan.overlay]))
        network.register_consumer(
            vm.process.pid, allocation["identity"], client=False, consumer_id="qemu:" + vm.plan.uuid
        )
        self.ledger.append(
            "resource-vm-bound",
            {"uuid": vm.plan.uuid, "allocation": allocation, "disks": disks, "direct_fds": fds},
        )
        return allocation
