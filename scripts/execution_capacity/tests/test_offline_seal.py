"""Offline-only image hashing and verified QEMU termination are mandatory."""

import os
from types import SimpleNamespace

import pytest


def test_hash_rejects_path_replacement_during_read(tmp_path, monkeypatch):
    from scripts.execution_capacity import seal_offline as seal

    path = tmp_path / "disk.raw"
    path.write_bytes(b"original")
    path.chmod(0o600)
    original = os.read
    changed = False

    def read(fd, size):
        nonlocal changed
        data = original(fd, size)
        if data and not changed:
            changed = True
            path.rename(tmp_path / "retained-old.raw")
            path.write_bytes(b"replacement")
        return data

    monkeypatch.setattr(seal.os, "read", read)
    with pytest.raises(ValueError, match="changed"):
        seal.hash_offline(path)


def test_shutdown_rejects_abnormal_exact_qemu_exit(monkeypatch):
    from scripts.execution_capacity import seal_finalizer as seal

    events = []

    class Ledger:
        def append(self, kind, body):
            events.append(kind)

        def records(self, kind):
            return []

    vm = SimpleNamespace(
        identity={"pid": 42},
        process=SimpleNamespace(pid=42),
        plan=SimpleNamespace(uuid="vm"),
        ledger=Ledger(),
        wait_exit=lambda seconds: 7,
    )
    monkeypatch.setattr(seal, "process_identity", lambda pid: {"pid": pid})
    agent = SimpleNamespace(shutdown=lambda: events.append("shutdown-send"))
    with pytest.raises(ValueError, match="abnormal"):
        seal.shutdown(vm, agent)
    assert events == ["guest-shutdown-intent", "shutdown-send"]


def test_qga_powerdown_is_fixed_no_response_command():
    from scripts.execution_capacity.reference_protocol import GuestAgent

    sent = []
    wire = SimpleNamespace(sendall=sent.append, settimeout=lambda value: None)

    def command(name):
        assert name == "guest-info"
        return {
            "supported_commands": [
                {"name": "guest-shutdown", "enabled": True, "success-response": False}
            ]
        }

    channel = SimpleNamespace(poisoned=False, wire=wire, command=command, timeout=2)
    GuestAgent(channel).shutdown()
    assert sent == [b'{"execute":"guest-shutdown","arguments":{"mode":"powerdown"}}\n']
    assert channel.poisoned


@pytest.mark.parametrize("fault", [None, "source_mutated", "extra_disk", "unclean_exit"])
def test_actual_flatten_commands_readback_and_retention(tmp_path, monkeypatch, fault):
    import json

    from scripts.execution_capacity import seal_offline as seal

    tmp_path.chmod(0o700)
    base, overlay, output = (tmp_path / name for name in ("base.raw", "round.qcow2", "sealed.raw"))
    for path, data in ((base, b"original"), (overlay, b"overlay")):
        path.write_bytes(data)
        path.chmod(0o600)
    rows = {
        "qemu-exited": [
            {"body": {"identity": {"pid": 42}, "returncode": 7 if fault == "unclean_exit" else 0}}
        ],
        "guest-shutdown-intent": [{"body": {"uuid": "owned"}}],
        "overlay-created": [{"body": {"uuid": "owned", "identity": seal.hash_offline(overlay)}}],
    }
    events = []

    class Ledger:
        def records(self, kind):
            return rows.get(kind, [])

        def append(self, kind, body):
            events.append(kind)
            rows.setdefault(kind, []).append({"body": body})

    plan = SimpleNamespace(
        uuid="owned",
        base=base,
        base_identity=seal.hash_offline(base),
        overlay=overlay,
        qemu_img_sha256="a" * 64,
    )
    vm = SimpleNamespace(ledger=Ledger(), plan=plan, identity={"pid": 42}, pidfd=None)
    monkeypatch.setattr(
        "scripts.execution_capacity.reference_vm.file_identity", lambda path: {"sha256": "a" * 64}
    )
    monkeypatch.setattr(seal, "no_writers", lambda paths: events.append("no-writers"))

    def command(args, timeout=30):
        events.append(args[0])
        if args[:3] == ["info", "--output=json", "--backing-chain"]:
            chain = [
                {
                    "format": "qcow2",
                    "filename": str(overlay),
                    "full-backing-filename": str(base),
                    "virtual-size": 8,
                },
                {"format": "raw", "filename": str(base), "virtual-size": 8},
            ]
            if fault == "extra_disk":
                chain.append({"format": "raw"})
            return json.dumps(chain).encode()
        if args[0] == "convert":
            assert args == ["convert", "-n", "-f", "qcow2", "-O", "raw", str(overlay), str(output)]
            assert rows["seal-flatten-intent"]
            output.write_bytes(b"flattened"[:8])
            if fault == "source_mutated":
                overlay.write_bytes(b"changed")
            return b""
        if args[0] == "compare":
            assert args == ["compare", "-f", "qcow2", "-F", "raw", str(overlay), str(output)]
            return b""
        assert args == ["info", "--output=json", "-f", "raw", str(output)]
        return b'{"format":"raw","virtual-size":8}'

    monkeypatch.setattr(seal, "command", command)
    if fault:
        with pytest.raises(ValueError, match=r"clean|graph|changed"):
            seal.flatten(vm, output)
        assert not rows.get("seal-flattened")
        if fault == "source_mutated":
            assert output.exists()
        if fault == "unclean_exit":
            assert events == []
    else:
        actual = seal.flatten(vm, output)
        assert actual["sha256"] == seal.hash_offline(output)["sha256"]
        assert (
            events.index("seal-flatten-intent")
            < events.index("convert")
            < events.index("compare")
            < events.index("seal-flattened")
        )
    assert base.exists()
    assert overlay.exists()


def test_offline_alias_images_are_rejected(tmp_path):
    from scripts.execution_capacity.seal_offline import no_writers

    base = tmp_path / "base"
    base.write_bytes(b"same")
    alias = tmp_path / "alias"
    alias.hardlink_to(base)
    with pytest.raises(ValueError, match="alias"):
        no_writers([base, alias], proc=tmp_path)
