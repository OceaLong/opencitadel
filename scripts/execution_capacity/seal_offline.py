"""Offline-only flatten/readback; never commit, rebase, delete, or hash live disks."""

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

from scripts.execution_capacity.ownership import _open_private, _private_directory


def stable(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def hash_offline(path):
    path = Path(path)
    if path.resolve() != path:
        raise ValueError("non-symlink offline image required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        first = os.fstat(fd)
        if (
            not stat.S_ISREG(first.st_mode)
            or first.st_nlink != 1
            or stat.S_IMODE(first.st_mode) & 0o077
        ):
            raise ValueError("private single-link offline image required")
        hashed = hashlib.sha256()
        while block := os.read(fd, 1024 * 1024):
            hashed.update(block)
        if stable(first) != stable(os.fstat(fd)) or stable(first) != stable(path.lstat()):
            raise ValueError("offline image changed while hashing")
        return {
            "device": first.st_dev,
            "inode": first.st_ino,
            "size_bytes": first.st_size,
            "sha256": hashed.hexdigest(),
        }
    finally:
        os.close(fd)


def no_writers(paths, *, proc=Path("/proc")):
    identities = {(p.stat().st_dev, p.stat().st_ino) for p in paths}
    if len(identities) != len(paths):
        raise ValueError("alias image inode")
    for process in proc.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            descriptors = list((process / "fd").iterdir())
        except FileNotFoundError:
            continue
        for fd in descriptors:
            try:
                info = fd.stat()
                if (info.st_dev, info.st_ino) not in identities:
                    continue
                fields = dict(
                    line.split(":", 1)
                    for line in (process / "fdinfo" / fd.name).read_text().splitlines()
                )
                if int(fields["flags"].strip(), 8) & os.O_ACCMODE != os.O_RDONLY:
                    raise ValueError("offline image still has an actual writable descriptor")
            except FileNotFoundError:
                continue
        # Permission errors deliberately fail closed: invisible processes do
        # not constitute absent writers. Reference host requires this authority.


def command(args, timeout=30):
    result = subprocess.run(
        ["/usr/bin/qemu-img", *args],
        capture_output=True,
        check=True,
        timeout=timeout,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
    )
    if result.stderr or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("offline qemu-img readback invalid")
    return result.stdout


def flatten(vm, output, *, timeout=3600):
    ledger, plan = vm.ledger, vm.plan
    exits = [
        r["body"] for r in ledger.records("qemu-exited") if r["body"]["identity"] == vm.identity
    ]
    if (
        len(exits) != 1
        or exits[0]["returncode"] != 0
        or vm.pidfd is not None
        or not ledger.records("guest-shutdown-intent")
        or ledger.records("qemu-force-exit-intent")
    ):
        raise ValueError("actual clean owned QEMU exit required before image reads")
    output = Path(output)
    _private_directory(output.parent)
    if output.resolve() != output or output.exists() or output in {plan.base, plan.overlay}:
        raise ValueError("fresh private flattened image path required")
    if not 1 <= timeout <= 3600:
        raise ValueError("fixed offline conversion bound required")
    from scripts.execution_capacity.reference_vm import file_identity

    if file_identity(Path("/usr/bin/qemu-img"))["sha256"] != plan.qemu_img_sha256:
        raise ValueError("pinned offline converter differs")
    no_writers([plan.base, plan.overlay])
    before = {str(p): hash_offline(p) for p in (plan.base, plan.overlay)}
    if before[str(plan.base)] != plan.base_identity:
        raise ValueError("original immutable base changed")
    created = [
        r["body"] for r in ledger.records("overlay-created") if r["body"]["uuid"] == plan.uuid
    ]
    if len(created) != 1 or any(
        before[str(plan.overlay)][k] != created[0]["identity"][k] for k in ("device", "inode")
    ):
        raise ValueError("actual owned overlay inode changed")
    chain = json.loads(command(["info", "--output=json", "--backing-chain", str(plan.overlay)]))
    if (
        len(chain) != 2
        or chain[0]["format"] != "qcow2"
        or chain[1]["format"] != "raw"
        or chain[0]["filename"] != str(plan.overlay)
        or chain[0]["full-backing-filename"] != str(plan.base)
        or chain[1]["filename"] != str(plan.base)
        or chain[0]["virtual-size"] != chain[1]["virtual-size"]
        or chain[1].get("backing-filename")
        or chain[0].get("snapshots")
    ):
        raise ValueError("unsupported actual offline block graph")
    size = chain[0]["virtual-size"]
    ledger.append(
        "seal-flatten-intent",
        {"uuid": plan.uuid, "input": before, "output": str(output), "virtual_size": size},
    )
    fd = _open_private(output, os.O_RDWR | os.O_CREAT | os.O_EXCL)
    try:
        os.ftruncate(fd, size)
        os.fsync(fd)
        created_output = stable(os.fstat(fd))[:2]
    finally:
        os.close(fd)
    command(["convert", "-n", "-f", "qcow2", "-O", "raw", str(plan.overlay), str(output)], timeout)
    if stable(output.stat())[:2] != created_output:
        raise ValueError("flatten output inode replaced")
    command(["compare", "-f", "qcow2", "-F", "raw", str(plan.overlay), str(output)], timeout)
    info = json.loads(command(["info", "--output=json", "-f", "raw", str(output)]))
    if info["format"] != "raw" or info["virtual-size"] != size or info.get("backing-filename"):
        raise ValueError("flatten output readback differs")
    no_writers([plan.base, plan.overlay, output])
    if before != {str(p): hash_offline(p) for p in (plan.base, plan.overlay)}:
        raise ValueError("source image changed during offline conversion")
    identity = hash_offline(output)
    if identity["size_bytes"] != size:
        raise ValueError("flattened raw file size differs")
    fd = os.open(output, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    directory = os.open(output.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    ledger.append(
        "seal-flattened", {"uuid": plan.uuid, "identity": identity, "originals_retained": True}
    )
    return identity
