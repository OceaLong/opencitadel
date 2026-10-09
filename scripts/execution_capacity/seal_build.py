"""Exhaustive installed filesystem and mounted source/config byte acquisition.

Docker export is read only and works after the exact container exits. Every tar
member contributes, including OS/native libraries and traced frontend runtime.
The exports and detailed paths stay private on the covered guest root disk.
"""

import hashlib
import json
import os
import stat
import tarfile
from pathlib import Path, PurePosixPath


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def read_tree(root):
    root = Path(root)
    if root.is_symlink() or root.resolve() != root:
        raise ValueError("source tree link refused")
    names = sorted([root] if root.is_file() else root.rglob("*"))
    digest = hashlib.sha256()
    files = 0
    snapshots = {}
    device = root.stat().st_dev
    for path in names:
        name = path.relative_to(root).as_posix()
        before = path.lstat()
        snapshots[path] = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        if before.st_dev != device:
            raise ValueError("external nested build mount")
        if stat.S_ISLNK(before.st_mode):
            if not path.resolve().is_relative_to(root):
                raise ValueError("external build tree link")
            body = [name, "link", os.readlink(path)]
        elif stat.S_ISDIR(before.st_mode):
            body = [name, "directory", stat.S_IMODE(before.st_mode)]
        elif stat.S_ISREG(before.st_mode):
            hashed = hashlib.sha256()
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    hashed.update(block)
            body = [name, before.st_size, stat.S_IMODE(before.st_mode), hashed.hexdigest()]
            files += 1
        else:
            raise ValueError("unsupported build tree object")
        after = path.lstat()
        if snapshots[path] != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("build tree mutated during acquisition")
        digest.update(encoded(body) + b"\n")
    if names != sorted([root] if root.is_file() else root.rglob("*")):
        raise ValueError("build tree membership changed")
    for path, expected in snapshots.items():
        final = path.lstat()
        if expected != (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
        ):
            raise ValueError("build tree changed before completion")
    return {"files": files, "digest": digest.hexdigest()}


def read_export(path, role):
    before = path.stat()
    digest, names, files = hashlib.sha256(), set(), 0
    with tarfile.open(path, "r|*") as archive:
        for item in archive:
            name = item.name.removeprefix("./").rstrip("/")
            pure = PurePosixPath(name)
            if not name or pure.is_absolute() or ".." in pure.parts or name in names:
                raise ValueError("ambiguous image archive path")
            names.add(name)
            body = [
                name,
                item.type.decode("ascii"),
                item.mode,
                item.uid,
                item.gid,
                item.size,
                item.linkname,
                item.pax_headers,
            ]
            if item.isfile():
                hashed = hashlib.sha256()
                stream = archive.extractfile(item)
                while block := stream.read(1024 * 1024):
                    hashed.update(block)
                body.append(hashed.hexdigest())
                files += 1
            digest.update(encoded(body) + b"\n")
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("image export mutated during acquisition")
    if role == "frontend" and not (
        "app/server.js" in names
        and any(n.startswith("app/.next/static/") for n in names)
        and any(n.startswith("app/node_modules/") for n in names)
    ):
        raise ValueError("actual frontend standalone/static/runtime missing")
    if role == "api" and not any(n.startswith("app/.venv/lib/") for n in names):
        raise ValueError("actual installed API dependencies missing")
    if not files:
        raise ValueError("empty actual image export")
    return {"files": files, "members": len(names), "digest": digest.hexdigest()}
