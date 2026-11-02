"""Guest resource observations use temporary proc/cgroup files and fixed subprocess transcripts."""

from pathlib import Path

import pytest


def test_pg_effective_limit_reads_current_ancestors_and_membership(tmp_path):
    from scripts.execution_capacity.guest_infrastructure import observe_pg

    proc, cgroup = tmp_path / "proc", tmp_path / "cgroup"
    (proc / "12").mkdir(parents=True)
    (proc / "12/cgroup").write_text("0::/owned/pg\n")
    for leaf, memory in [
        (cgroup, "max"),
        (cgroup / "owned", str(16 * 1024**3)),
        (cgroup / "owned/pg", str(8 * 1024**3)),
    ]:
        leaf.mkdir(exist_ok=True)
        (leaf / "memory.max").write_text(memory)
    for name, value in {
        "memory.swap.max": "0",
        "memory.current": "1234",
        "cpu.stat": "usage_usec 42",
        "cgroup.procs": "12\n13\n",
        "memory.events": "oom 0",
    }.items():
        (cgroup / "owned/pg" / name).write_text(value)
    container = {"State": {"Pid": 12}, "HostConfig": {"Memory": 8 * 1024**3}}
    got = observe_pg(container, proc=proc, cgroup=cgroup)
    assert got["effective_memory_bytes"] == 8589934592
    (cgroup / "owned/memory.max").write_text(str(4 * 1024**3))
    with pytest.raises(ValueError, match="PostgreSQL"):
        observe_pg(container, proc=proc, cgroup=cgroup)


def test_service_endpoint_must_be_listener_owned_by_exact_process(tmp_path):
    from scripts.execution_capacity.guest_infrastructure import listener_identity

    proc = tmp_path / "proc"
    (proc / "8/fd").mkdir(parents=True)
    (proc / "8/net").mkdir()
    (proc / "8/fd/4").symlink_to("socket:[443]")
    # /proc/net/tcp little-endian 10.0.2.15, port 43191=A8B7, LISTEN=0A.
    (proc / "8/net/tcp").write_text("header\n0: 0F02000A:A8B7 00000000:0000 0A 0:0 0:0 0 0 0 443\n")
    assert listener_identity(8, proc=proc)["socket_inode"] == 443
    (proc / "8/fd/4").unlink()
    (proc / "8/fd/4").symlink_to("socket:[999]")
    with pytest.raises(ValueError, match="listener"):
        listener_identity(8, proc=proc)


@pytest.mark.parametrize(
    "race", ["none", "replacement-running", "replacement-exited", "no-exit", "signal-failure"]
)
def test_stop_requires_original_service_and_observed_pidfd_exit(tmp_path, monkeypatch, race):
    import hashlib
    import select
    import signal
    from types import SimpleNamespace

    from scripts.execution_capacity import guest_infrastructure as mod

    group_root = tmp_path / "cgroup"
    group = group_root / "system.slice" / mod.UNIT
    group.mkdir(parents=True)
    for name, value in {
        "cgroup.procs": "8",
        "memory.max": "268435456",
        "memory.swap.max": "0",
        "cpu.max": "100000 100000",
        "memory.current": "9000",
        "memory.events": "oom 0",
        "cpu.stat": "usage_usec 15",
        "cgroup.events": "populated 1",
    }.items():
        (group / name).write_text(value)
    monkeypatch.setattr(mod, "CGROUP", group_root)
    monkeypatch.setattr(mod, "pinned_file", lambda path, expected: expected)
    monkeypatch.setattr(
        mod,
        "listener_identity",
        lambda pid: {"address": "10.0.2.15", "port": 43191, "socket_inode": 44},
    )
    original_read = Path.read_text

    def read(path, *args, **kwargs):
        if str(path) == "/proc/sys/kernel/random/boot_id":
            return "boot"
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    raw = b"/usr/bin/python3\0-I\0/opt/opencitadel-capacity/calibration.py\0serve\0"
    process = {
        "pid": 8,
        "start_ticks": 42,
        "argv_digest": hashlib.sha256(raw).hexdigest(),
        "executable_sha256": "a" * 64,
        "cgroup": "0::/system.slice/" + mod.UNIT,
    }
    server = {"pid": 8, "start_ticks": 42, "boot_id": "boot", "service_sha256": "b" * 64}
    manifest = {
        "calibration": {
            "python_sha256": "a" * 64,
            "service_sha256": "b" * 64,
            "unit_sha256": "c" * 64,
            "infrastructure_sha256": "d" * 64,
        }
    }
    physical = {
        "pid": 8,
        "exec_pid": 8,
        "generation_start": "42000",
        "old_exited": False,
        "snapshot_count": 0,
        "signals": [],
        "unit_stop_targets": [],
    }

    def snapshot(pid):
        assert pid == 8
        physical["snapshot_count"] += 1
        if physical["snapshot_count"] == 3 and race.startswith("replacement"):
            # P passes the last identity read, then exits before the stop effect.
            # A separate operator activates Q under the same mutable unit name.
            physical.update(pid=9, exec_pid=9, generation_start="99000", old_exited=True)
            (group / "cgroup.procs").write_text("9")
            if race == "replacement-exited":
                physical["pid"] = 0
                (group / "cgroup.events").write_text("populated 0")
        return process

    def run(argv, **kwargs):
        assert argv[0] == "/usr/bin/systemctl"
        assert argv[2] == mod.UNIT
        if argv[1] == "stop":
            # Model the defect: systemctl targets whichever process is current.
            physical["unit_stop_targets"].append(physical["pid"])
            physical.update(pid=0, old_exited=True)
            (group / "cgroup.events").write_text("populated 0")
            return SimpleNamespace(stdout=b"")
        assert argv[1] == "show"
        state = {
            "LoadState": "loaded",
            "ActiveState": "active" if physical["pid"] else "inactive",
            "SubState": "running" if physical["pid"] else "dead",
            "MainPID": str(physical["pid"]),
            "ControlGroup": "/system.slice/" + mod.UNIT,
            "Result": "success",
            "NRestarts": "0",
            "ExecMainPID": str(physical["exec_pid"]),
            "ExecMainStartTimestampMonotonic": physical["generation_start"],
        }
        return SimpleNamespace(stdout="\n".join(f"{k}={v}" for k, v in state.items()).encode())

    def send_signal(fd, sig):
        assert fd == 100
        assert sig == signal.SIGTERM
        physical["signals"].append((fd, sig))
        if physical["old_exited"]:
            raise ProcessLookupError("the original pidfd target P has exited")
        if race == "signal-failure":
            raise PermissionError("delegation unavailable")
        if race != "no-exit":
            physical.update(pid=0, old_exited=True)
            (group / "cgroup.events").write_text("populated 0")

    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod.os, "pidfd_open", lambda pid: 100, raising=False)
    monkeypatch.setattr(signal, "pidfd_send_signal", send_signal, raising=False)
    closed = []
    real_close = mod.os.close

    def close(fd):
        if fd == 100:
            closed.append(fd)
        else:
            real_close(fd)

    monkeypatch.setattr(mod.os, "close", close)

    class Poll:
        def register(self, fd, event):
            assert fd == 100

        def poll(self, timeout):
            return [(100, select.POLLIN)] if physical["old_exited"] else []

    monkeypatch.setattr(select, "poll", Poll)
    request = {"identity": {"boot_id": "boot"}, "phase": "stop", "server": {**server, "pid": 9}}
    with pytest.raises(ValueError, match="host-owned stop identity"):
        mod.infrastructure(request, manifest, [], snapshot)
    assert not physical["signals"]
    assert not physical["unit_stop_targets"]
    physical["snapshot_count"] = 0
    request["server"] = server
    if race == "none":
        result = mod.infrastructure(request, manifest, [], snapshot)
        assert result["disposition"] == "service-exit-observed"
        assert result["before"]["server"] == server
        assert result["after"]["MainPID"] == "0"
    else:
        expected_error = PermissionError if race == "signal-failure" else ValueError
        with pytest.raises(expected_error, match=r"retained|delegation"):
            mod.infrastructure(request, manifest, [], snapshot)
    assert physical["signals"] == [(100, signal.SIGTERM)]
    assert physical["unit_stop_targets"] == []
    if race == "replacement-running":
        assert physical["pid"] == 9  # Q remains untouched; no clean stop receipt.
    assert closed == [100]


def test_os_infrastructure_action_is_not_accepted_by_container_cli():
    from scripts.execution_capacity.guest_main import parser

    with pytest.raises(SystemExit, match="2"):
        parser().parse_args(["infrastructure", "{}"])
