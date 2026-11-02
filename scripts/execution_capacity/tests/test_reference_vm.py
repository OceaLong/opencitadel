"""Pure command/real parser checks. No QEMU, subprocess or socket invocation."""

import json
from uuid import uuid4

import pytest
from scripts.execution_capacity.reference_protocol import ProtocolError
from scripts.execution_capacity.reference_vm import VMPlan, verify_nodes


def plan(tmp_path):
    return VMPlan(
        str(uuid4()),
        "pc-q35-10.1",
        "a" * 64,
        "b" * 64,
        tmp_path / "bios",
        "c" * 64,
        tmp_path / "base",
        {},
        tmp_path / "overlay",
        tmp_path / "qmp",
        tmp_path / "qga",
        "192.168.201.1",
        (8000, 3000),
        tuple(range(16)),
    )


def test_closed_vm_command_requires_kvm_and_direct_explicit_graph(tmp_path):
    args = plan(tmp_path).argv()
    assert "-S" in args
    assert "pc-q35-10.1,accel=kvm" in args
    nodes = [json.loads(args[i + 1]) for i, v in enumerate(args) if v == "-blockdev"]
    assert {n["node-name"] for n in nodes} == {"base-file", "base", "round-file", "round-root"}
    assert all(n["cache"] == {"direct": True, "no-flush": False} for n in nodes)
    assert nodes[-1]["backing"] == "base"
    assert (
        args[args.index("-netdev") + 1]
        == "user,id=owned,restrict=on,hostfwd=tcp:192.168.201.1:8000-:8000,hostfwd=tcp:192.168.201.1:3000-:3000"
    )
    assert "-loadvm" not in args
    assert "-virtfs" not in args


def nodes(p):
    result = []
    for name, driver, children, readonly, path in [
        ("base-file", "file", {}, True, p.base),
        ("base", "raw", {"file": "base-file"}, True, p.base),
        ("round-file", "file", {}, False, p.overlay),
        ("round-root", "qcow2", {"file": "round-file", "backing": "base"}, False, p.overlay),
    ]:
        result.append(
            {
                "node-name": name,
                "drv": driver,
                "children": [{"name": k, "info": {"node-name": v}} for k, v in children.items()],
                "ro": readonly,
                "cache": {"direct": True, "no-flush": False, "writeback": True},
                "file": str(path),
            }
        )
    return result


def test_actual_graph_rejects_shared_cached_backing_and_missing_fd(tmp_path):
    p = plan(tmp_path)
    p.base.touch()
    p.overlay.touch()
    fds = {(f.stat().st_dev, f.stat().st_ino) for f in (p.base, p.overlay)}
    assert len(verify_nodes(nodes(p), p, fds)) == 4
    wrong = nodes(p)
    wrong[0]["cache"]["direct"] = False
    with pytest.raises(ProtocolError, match="cache"):
        verify_nodes(wrong, p, fds)
    with pytest.raises(ProtocolError, match="descriptor"):
        verify_nodes(nodes(p), p, set())
    wrong = nodes(p)
    wrong[-1]["children"][-1]["info"]["node-name"] = "foreign"
    with pytest.raises(ProtocolError, match="child"):
        verify_nodes(wrong, p, fds)
