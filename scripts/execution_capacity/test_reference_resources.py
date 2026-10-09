"""Pure resource parsers and temporary cgroup-file boundary transcripts."""

from pathlib import Path

import pytest


def test_effective_constraints_include_ancestors_not_only_leaf(tmp_path):
    from scripts.execution_capacity.reference_resources import cgroup_snapshot, validate_envelope

    root = tmp_path / "cgroup"
    leaf = root / "owned"
    leaf.mkdir(parents=True)
    for path in (root, leaf):
        for name, value in {
            "cpuset.cpus.effective": "0-15",
            "cpuset.mems.effective": "0",
            "cpu.max": "max 100000",
            "memory.max": "max",
            "memory.swap.max": "0",
            "memory.current": "12",
            "memory.events": "low 0\noom 0\n",
            "cpu.stat": "usage_usec 44\n",
            "cgroup.procs": "123\n",
            "cgroup.events": "populated 1\n",
        }.items():
            (path / name).write_text(value)
    (leaf / "memory.max").write_text(str(34 * 1024**3))
    got = cgroup_snapshot(leaf, mount=root)
    validate_envelope(got, tuple(range(16)), 34 * 1024**3)
    (root / "cpu.max").write_text("800000 100000")
    with pytest.raises(ValueError, match="CPU quota"):
        validate_envelope(cgroup_snapshot(leaf, mount=root), tuple(range(16)), 34 * 1024**3)
    (root / "cpu.max").write_text("max 100000")
    (root / "memory.max").write_text(str(16 * 1024**3))
    with pytest.raises(ValueError, match="memory"):
        validate_envelope(cgroup_snapshot(leaf, mount=root), tuple(range(16)), 34 * 1024**3)


def test_disjoint_cpus_and_real_host_headroom_required():
    from scripts.execution_capacity.reference_resources import validate_capacity

    capacity = {
        "architecture": "x86_64",
        "online_cpus": list(range(24)),
        "memory_bytes": 64 * 1024**3,
        "memory_available_bytes": 64 * 1024**3,
        "kvm_api": 12,
        "topology": {str(i): [0, i] for i in range(24)},
    }
    validate_capacity(capacity, tuple(range(16)), (16, 17, 18, 19), 2 * 1024**3, 4 * 1024**3)
    with pytest.raises(ValueError, match="disjoint"):
        validate_capacity(capacity, tuple(range(16)), (15, 16, 17, 18), 2 * 1024**3, 4 * 1024**3)
    capacity["memory_bytes"] = 48 * 1024**3
    with pytest.raises(ValueError, match="headroom"):
        validate_capacity(capacity, tuple(range(16)), (16, 17, 18, 19), 2 * 1024**3, 4 * 1024**3)


def test_guest_pg_cgroup_membership_cannot_be_configuration_only(tmp_path):
    from scripts.execution_capacity.reference_resources import validate_pg

    observed = {
        "container_memory_bytes": 8 * 1024**3,
        "pid": 12,
        "membership": "/docker/owned",
        "processes": [12, 13],
        "effective_memory_bytes": 8 * 1024**3,
        "swap_max": 0,
    }
    validate_pg(observed)
    observed["effective_memory_bytes"] = 4 * 1024**3
    with pytest.raises(ValueError, match="PostgreSQL"):
        validate_pg(observed)
    observed["effective_memory_bytes"] = 8 * 1024**3
    observed["processes"] = []
    with pytest.raises(ValueError, match="PostgreSQL"):
        validate_pg(observed)


def test_storage_rejects_virtual_rotational_zero_without_hardware_proof(tmp_path, monkeypatch):
    from scripts.execution_capacity.reference_resources import storage_backing

    disk = tmp_path / "disk"
    disk.touch()
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        if str(path).startswith("/sys/dev/block/"):
            return Path("/sys/devices/virtual/block/vda")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="physical host PCI NVMe"):
        storage_backing(disk)


def test_capacity_rejects_server_client_smt_sibling_overlap():
    from scripts.execution_capacity.reference_resources import validate_capacity

    capacity = {
        "architecture": "x86_64",
        "online_cpus": list(range(24)),
        "memory_bytes": 64 * 1024**3,
        "memory_available_bytes": 64 * 1024**3,
        "kvm_api": 12,
        "topology": {str(i): [0, i] for i in range(24)},
    }
    capacity["topology"]["16"] = [0, 0]
    with pytest.raises(ValueError, match="physical core"):
        validate_capacity(capacity, tuple(range(16)), (16, 17, 18, 19), 2 * 1024**3, 4 * 1024**3)


def test_configure_writes_only_preregistered_empty_isolated_groups(tmp_path, monkeypatch):
    from scripts.execution_capacity import reference_resources as mod
    from scripts.execution_capacity.attempt import AttemptLedger

    root = tmp_path / "cgroup"
    root.mkdir()
    monkeypatch.setattr(mod, "CGROUP", root)
    groups = {}
    for role, cpu_ids in [("server", list(range(16))), ("client", list(range(16, 20)))]:
        group = root / role
        group.mkdir()
        for name, value in {
            "cgroup.procs": "",
            "cpuset.cpus.partition": "isolated",
            "cpuset.cpus.effective": ",".join(map(str, cpu_ids)),
            "cpuset.mems.effective": "0",
            "memory.current": "0",
            "memory.events": "oom 0",
            "cpu.stat": "usage_usec 0",
            "cgroup.events": "populated 0",
        }.items():
            (group / name).write_text(value)
        info = group.stat()
        groups[role] = {
            "path": str(group),
            "cpus": cpu_ids,
            "identity": {"device": info.st_dev, "inode": info.st_ino},
        }
    plan = {
        "resources": {
            "topology": "same-host-linux-x86-kvm",
            "groups": groups,
            "qemu_overhead_bytes": 2 * 1024**3,
            "host_headroom_bytes": 4 * 1024**3,
        }
    }

    def hardware():
        return {
            "architecture": "x86_64",
            "online_cpus": list(range(24)),
            "memory_bytes": 64 * 1024**3,
            "memory_available_bytes": 64 * 1024**3,
            "kvm_api": 12,
            "topology": {str(i): [0, i] for i in range(24)},
        }

    monkeypatch.setattr(mod, "host_capacity", hardware)
    with AttemptLedger.create(tmp_path / "attempt", plan) as ledger:
        allocation = mod.ResourceAllocation(ledger)
        result = allocation.configure()
        assert result["server"]["ancestors"][0]["memory.max"] == "36507222016"
        assert result["client"]["ancestors"][0]["memory.max"] == "17179869184"
        assert (root / "server/memory.min").read_text() == "36507222016"
        assert (root / "client/memory.swap.max").read_text() == "0"
        with pytest.raises(ValueError, match="consumed"):
            allocation.configure()
        (root / "client/cpuset.cpus.partition").write_text("member")
        with pytest.raises(ValueError, match="isolated"):
            allocation.observe()
