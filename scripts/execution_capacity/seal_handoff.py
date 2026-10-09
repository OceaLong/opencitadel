"""Optional capacity seed lifetime handoff; normal seed behavior is unchanged."""

import asyncio
import os
import socket
import time
from pathlib import Path

from scripts.execution_capacity.guest_seal import read_private, write_private


def publish(root, identity, *, deadline_ns):
    return write_private(
        root / "seal-ready.json", {"identity": identity, "deadline_ns": deadline_ns}
    )


def release(root, identity, *, now_ns):
    ready = read_private(root / "seal-ready.json")
    if ready["identity"] != identity or now_ns >= ready["deadline_ns"]:
        raise ValueError("captured seed identity/deadline differs")
    write_private(root / "seal-release.json", ready)


async def wait_release(root, identity, *, clock=time.monotonic_ns):
    ready = read_private(root / "seal-ready.json")
    if ready["identity"] != identity:
        raise ValueError("seed handoff identity differs")
    while True:
        if clock() >= ready["deadline_ns"]:
            raise TimeoutError("seed capture deadline expired; retained")
        if (root / "seal-release.json").exists():
            if read_private(root / "seal-release.json") != ready:
                raise ValueError("seed capture release differs")
            return
        await asyncio.sleep(0.01)


async def child_handoff(binding, root):
    rule = binding.get("seal")
    if rule is None:
        return
    if not 1 <= rule["handoff_timeout_seconds"] <= 300:
        raise ValueError("fixed seed handoff bound required")
    proc = Path("/proc/self")
    fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
    identity = {
        "attempt_id": os.environ["CAPACITY_ATTEMPT_ID"],
        "boot_id": (
            await asyncio.to_thread(Path("/proc/sys/kernel/random/boot_id").read_text)
        ).strip(),
        "source_digest": binding["source_sha256"],
        "pid": os.getpid(),
        "start_ticks": int(fields[19]),
        "pid_namespace": (proc / "ns/pid").stat().st_ino,
        "hostname": socket.gethostname(),
    }
    publish(
        root,
        identity,
        deadline_ns=time.monotonic_ns() + rule["handoff_timeout_seconds"] * 1_000_000_000,
    )
    print("capacity-seal-ready", flush=True)
    await wait_release(root, identity)


def parent_ready(deployment, root, attempt_id):
    """Called by seed host's attach loop, before any child wait/restore path."""
    import json

    from scripts.execution_capacity.guest_bridge import process_snapshot
    from scripts.execution_capacity.guest_seal import incarnation
    from scripts.execution_capacity.host import docker
    from scripts.execution_capacity.writer_lifecycle import PROCESS_READ

    ready = read_private(root / "seal-ready.json")
    identity = ready["identity"]
    _, rows = deployment.verify(stopped=True)
    child = rows[deployment.child]
    if (
        identity["attempt_id"] != attempt_id
        or identity["source_digest"] != deployment.binding["source_sha256"]
        or child["Config"]["Hostname"] != identity["hostname"]
        or not child["State"]["Running"]
        or time.monotonic_ns() >= ready["deadline_ns"]
    ):
        raise ValueError("actual seed ready identity differs at parent")
    observed = json.loads(
        docker("exec", deployment.child, "python", "-c", PROCESS_READ, str(identity["pid"]))
    )
    if any(observed[k] != identity[k] for k in ("pid", "start_ticks", "pid_namespace", "boot_id")):
        raise ValueError("seed process changed before parent handoff")
    write_private(
        root / "seal-parent-ready.json",
        {"ready": ready, "child": incarnation(child), "parent": process_snapshot(os.getpid())},
    )


def parent_done(root, result):
    """Seed CLI calls this only after run_host's journal contexts have closed."""
    from scripts.execution_capacity.guest_bridge import process_snapshot

    handoff = read_private(root / "seal-parent-ready.json")
    if (
        process_snapshot(os.getpid()) != handoff["parent"]
        or result.get("attempt_id") != handoff["ready"]["identity"]["attempt_id"]
        or result.get("restoration") != "withheld_for_offline_seal"
    ):
        raise ValueError("original seed parent completion differs")
    write_private(
        root / "seal-parent-done.json",
        {"parent": handoff["parent"], "ready": handoff["ready"], "journals_closed": True},
    )
