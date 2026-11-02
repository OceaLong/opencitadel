"""Pure actual Docker-inspect parser coverage; subprocess substituted."""

import json
from hashlib import sha256
from types import SimpleNamespace

import pytest
from scripts.execution_capacity.guest_bridge import encoded, inspect_owned


def test_container_readback_checks_image_command_mounts_and_environment(monkeypatch):
    expected = {
        "id": "a" * 64,
        "image": "sha256:" + "b" * 64,
        "argv": ["python", "-m", "kernel"],
        "user": "1000:1000",
        "mounts": [{"source": "/private/source", "destination": "/capacity", "rw": False}],
        "env_digest": sha256(encoded(["ENV=test"])).hexdigest(),
        "network_ids": ["net"],
    }
    row = {
        "Id": expected["id"],
        "Image": expected["image"],
        "Path": "python",
        "Args": ["-m", "kernel"],
        "Config": {"User": "1000:1000", "Env": ["ENV=test"]},
        "Mounts": [{"Source": "/private/source", "Destination": "/capacity", "RW": False}],
        "NetworkSettings": {"Networks": {"owned": {"NetworkID": "net"}}},
        "State": {"Running": True},
        "HostConfig": {"Privileged": False},
    }

    def fake(command, **kwargs):
        assert command == ["/usr/bin/docker", "inspect", "--type", "container", "a" * 64]
        assert kwargs["timeout"] == 5
        return SimpleNamespace(stdout=json.dumps([row]).encode())

    monkeypatch.setattr("scripts.execution_capacity.guest_bridge.subprocess.run", fake)
    assert inspect_owned(expected)["Id"] == expected["id"]
    row["Mounts"][0]["RW"] = True
    with pytest.raises(ValueError, match="differs"):
        inspect_owned(expected)
    row["Mounts"][0]["RW"] = False
    row["Config"]["Env"] = ["ENV=production"]
    with pytest.raises(ValueError, match="differs"):
        inspect_owned(expected)


def test_discovery_matches_exact_start_identity_and_retains_process_start(tmp_path):
    from scripts.execution_capacity.guest_bridge import discover_start

    identity = {
        "attempt_id": "a",
        "sample_id": "s",
        "window_id": "w",
        "nonce": "n",
        "boot_id": "boot",
        "source_digest": "d",
    }
    proc = tmp_path / "31"
    proc.mkdir()
    argv = [
        "/usr/bin/python3",
        "-I",
        "/opt/opencitadel-capacity/guest_bridge.py",
        "cold-window",
        json.dumps({"identity": identity}),
    ]
    (proc / "cmdline").write_bytes(b"\0".join(x.encode() for x in argv) + b"\0")
    (proc / "stat").write_text(
        "31 (python (helper)) S " + " ".join(["0"] * 18 + ["4321"] + ["0"] * 8)
    )
    (proc / "cgroup").write_text("0::/owned/helper\n")
    executable = tmp_path / "python"
    executable.write_bytes(b"binary")
    (proc / "exe").symlink_to(executable)
    result = discover_start(identity, proc_root=tmp_path)
    assert result["pid"] == 31
    assert result["start_ticks"] == 4321
    assert result["cgroup"] == "0::/owned/helper"
    assert discover_start({**identity, "nonce": "other"}, proc_root=tmp_path) is None
    import shutil

    shutil.copytree(proc, tmp_path / "32", symlinks=True)
    with pytest.raises(ValueError, match="multiple"):
        discover_start(identity, proc_root=tmp_path)


def test_process_executable_is_opened_through_procfs_not_observer_path(tmp_path, monkeypatch):
    from pathlib import Path

    from scripts.execution_capacity.guest_bridge import process_snapshot

    proc = tmp_path / "81"
    proc.mkdir()
    (proc / "cmdline").write_bytes(b"python\0")
    (proc / "stat").write_text("81 (python) S " + " ".join(["0"] * 18 + ["912"] + ["0"] * 8))
    (proc / "cgroup").write_text("0::/container\n")
    executable = tmp_path / "actual-inode"
    executable.write_bytes(b"container executable")
    (proc / "exe").symlink_to(executable)
    original_resolve = Path.resolve

    def wrong_observer_namespace(path, *args, **kwargs):
        if path == proc / "exe":
            return tmp_path / "not-visible-in-observer-namespace"
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", wrong_observer_namespace)
    row = process_snapshot(81, proc_root=tmp_path)
    assert row["executable_sha256"] == sha256(b"container executable").hexdigest()
    assert row["executable_inode"] == executable.stat().st_ino


def test_process_executable_inode_change_during_read_is_rejected(tmp_path, monkeypatch):
    import os

    from scripts.execution_capacity.guest_bridge import process_snapshot

    proc = tmp_path / "81"
    proc.mkdir()
    (proc / "cmdline").write_bytes(b"python\0")
    (proc / "stat").write_text("81 (python) S " + " ".join(["0"] * 18 + ["912"] + ["0"] * 8))
    (proc / "cgroup").write_text("0::/container\n")
    first, replacement = tmp_path / "first", tmp_path / "replacement"
    first.write_bytes(b"original executable")
    replacement.write_bytes(b"replacement executable")
    (proc / "exe").symlink_to(first)
    read = os.read
    changed = False

    def replace_inode(fd, size):
        nonlocal changed
        value = read(fd, size)
        if not changed:
            changed = True
            (proc / "exe").unlink()
            (proc / "exe").symlink_to(replacement)
        return value

    monkeypatch.setattr("scripts.execution_capacity.guest_bridge.os.read", replace_inode)
    with pytest.raises(ValueError, match="executable changed"):
        process_snapshot(81, proc_root=tmp_path)
