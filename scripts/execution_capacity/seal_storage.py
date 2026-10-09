"""Actual guest filesystem/block readbacks for a single root-contained seal.

Called only by the fixed provisioned seal helper. No declared mapping is proof.
Unsupported network, shared, device-mapper and multi-filesystem layouts fail.
"""

import json
import os
import subprocess
from itertools import chain
from pathlib import Path


def command(argv):
    result = subprocess.run(
        argv,
        capture_output=True,
        check=True,
        timeout=10,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
    )
    if result.stderr or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("storage readback failed or exceeded bound")
    return result.stdout


def mount_readback(path):
    path = Path(path)
    if not path.is_absolute() or not path.exists():
        raise ValueError("actual absolute persistence path required")
    row = json.loads(
        command(
            [
                "/usr/bin/findmnt",
                "--json",
                "--target",
                str(path.resolve()),
                "--output",
                "TARGET,SOURCE,FSTYPE,MAJ:MIN",
            ]
        )
    )["filesystems"]
    if len(row) != 1:
        raise ValueError("ambiguous persistence mount")
    row = row[0]
    major_minor = row["maj:min"]
    actual = path.stat()
    if major_minor != f"{os.major(actual.st_dev)}:{os.minor(actual.st_dev)}":
        raise ValueError("actual persistence inode/mount device differs")
    device = Path("/sys/dev/block") / major_minor
    resolved = device.resolve(strict=True)
    disk = resolved.parent if (resolved / "partition").exists() else resolved
    if list((disk / "slaves").iterdir()) or "/virtual/" in str(disk):
        raise ValueError("external or composite persistence block device")
    serial = (disk / "serial").read_text().strip()
    return {
        "major_minor": major_minor,
        "fstype": row["fstype"],
        "source": row["source"],
        "disk": disk.name,
        "serial": serial,
    }


def validate_coverage(root, paths, serial):
    if root["fstype"] not in {"ext4", "xfs"} or root["serial"] != serial:
        raise ValueError("root filesystem/disk identity differs")
    roles = {p["role"] for p in paths}
    core = [
        p
        for p in paths
        if p["role"]
        in {"os", "datastore", "objects", "redis", "writers", "source", "docker", "wal"}
    ]
    if len(core) != 8 or len({p["path"] for p in core}) != 8:
        raise ValueError("missing/duplicate/alias persistence role")
    if not {"os", "datastore", "wal", "objects", "redis", "writers", "docker", "source"} <= roles:
        raise ValueError("complete actual persistence coverage missing")
    for row in paths:
        if not row["path"].startswith("/") or row["mount"] != root:
            raise ValueError("external/shared/split persistence storage")
    return {"root": root, "paths": paths}


def mapped_path(row, destination):
    """Translate an effective in-container location through actual Docker mounts."""
    target = Path(destination)
    matches = [m for m in row["Mounts"] if target.is_relative_to(m["Destination"])]
    if not target.is_absolute() or not matches:
        raise ValueError("persistence is not explicitly mounted")
    mount = max(matches, key=lambda m: len(m["Destination"]))
    if mount["Type"] not in {"bind", "volume"} or mount["RW"] is not True:
        raise ValueError("unsupported persistence mount")
    if mount["Type"] == "volume":
        volumes = json.loads(command(["/usr/bin/docker", "volume", "inspect", mount["Name"]]))
        if (
            len(volumes) != 1
            or volumes[0]["Driver"] != "local"
            or volumes[0].get("Options")
            or volumes[0]["Mountpoint"] != mount["Source"]
        ):
            raise ValueError("external/shared volume driver or mount")
    result = Path(mount["Source"]) / target.relative_to(mount["Destination"])
    if not result.exists():
        raise ValueError("actual persistence location absent")
    return result


def verify_writable_namespace(row, *, proc_root=Path("/proc")):
    """Reject hidden container-only mounts below the inspected persistence roots."""
    if row["State"]["Running"] is not True:
        return  # Offline readback retains the original verified mount configuration.
    pid = row["State"]["Pid"]
    if type(pid) is not int or pid <= 0:
        raise ValueError("actual persistence namespace process missing")
    process = proc_root / str(pid)
    with (process / "mountinfo").open() as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("persistence namespace inventory exceeds bound")
    mounts = set()
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) < 10 or "-" not in fields:
            raise ValueError("persistence namespace mount inventory incomplete")
        name = fields[4]
        for encoded, decoded in (
            (r"\040", " "),
            (r"\011", "\t"),
            (r"\012", "\n"),
            (r"\134", chr(92)),
        ):
            name = name.replace(encoded, decoded)
        target = Path(name)
        if not target.is_absolute() or ".." in target.parts:
            raise ValueError("persistence namespace mount path invalid")
        mounts.add(target)
    for mount in row["Mounts"]:
        target = Path(mount["Destination"])
        if not mount["RW"] or (target == Path("/var/run/docker.sock") and mount["Type"] == "bind"):
            continue
        if target not in mounts or any(p != target and p.is_relative_to(target) for p in mounts):
            raise ValueError("hidden nested persistence namespace mount")
        source = Path(mount["Source"]).stat()
        actual = (process / "root" / target.relative_to("/")).stat()
        if (source.st_dev, source.st_ino) != (actual.st_dev, actual.st_ino):
            raise ValueError("actual persistence namespace differs from inspected mount")
    if (process / "mountinfo").read_text() != raw:
        raise ValueError("persistence namespace changed during readback")


def acquire_paths(config, rows, pg, redis):
    pgrow, redisrow, miniorow = (
        rows[config["services"][key]] for key in ("postgres", "redis", "minio")
    )
    data = mapped_path(pgrow, pg["data_directory"])
    wal = data / "pg_wal"
    if wal.is_symlink():
        target = Path(os.readlink(wal))
        wal = mapped_path(
            pgrow, str(target if target.is_absolute() else Path(pg["data_directory"]) / target)
        )
    tablespaces = pg["tablespaces"]
    actual_links = sorted(p.name for p in (data / "pg_tblspc").iterdir())
    if actual_links != sorted(str(p["oid"]) for p in tablespaces):
        raise ValueError("actual complete tablespace inventory differs")
    paths = [("os", Path("/")), ("datastore", data), ("wal", wal)]
    allowed_links = {data / "pg_wal"} if (data / "pg_wal").is_symlink() else set()
    for item in tablespaces:
        link = data / "pg_tblspc" / str(item["oid"])
        if not link.is_symlink() or os.readlink(link) != item["path"]:
            raise ValueError("actual tablespace link differs")
        allowed_links.add(link)
        paths.append(("tablespace", mapped_path(pgrow, item["path"])))
    argv = [miniorow["Path"], *miniorow["Args"]]
    if "server" not in argv or argv[argv.index("server") + 1] != "/data":
        raise ValueError("unsupported actual Minio data placement")
    if (
        redis["appendonly"] != "yes"
        or not redis["appenddirname"]
        or Path(redis["dbfilename"]).name != redis["dbfilename"]
        or Path(redis["appenddirname"]).name != redis["appenddirname"]
    ):
        raise ValueError("actual Redis persistence configuration unsupported")
    redisdir = mapped_path(redisrow, redis["dir"])
    if not (redisdir / redis["appenddirname"]).is_dir():
        raise ValueError("actual Redis appendonly directory absent")
    info = json.loads(command(["/usr/bin/docker", "info", "--format", "{{json .}}"]))
    paths += [
        ("objects", mapped_path(miniorow, "/data")),
        ("redis", redisdir),
        ("writers", Path(config["writer_root"])),
        ("docker", Path(info["DockerRootDir"])),
        ("source", Path(config["source_root"])),
    ]
    broker = rows[config["services"]["broker"]]
    journal = "/var/lib/opencitadel-evaluation/operations.sqlite"
    paths.append(("broker-journal", mapped_path(broker, journal)))
    for suffix in ("-wal", "-shm", "-journal"):
        # Absent sidecars are ordinary SQLite state; dangling links are not.
        sidecar = mapped_path(broker, str(Path(journal).parent)) / (Path(journal).name + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            paths.append(("broker-journal", mapped_path(broker, journal + suffix)))
    paths.extend(
        ("journals", Path(p))
        for p in sorted({config["evidence_root"], config["seed_root"], *config["journal_roots"]})
    )
    # Every explicit mount is inspected, including observer/runtime/private
    # journals and source/config binds. Docker socket is a control endpoint,
    # never persistence; every other non-regular endpoint is rejected.
    for row in rows.values():
        verify_writable_namespace(row)
        for mount in row["Mounts"]:
            if mount["Destination"] == "/var/run/docker.sock" and mount["Type"] == "bind":
                continue
            if mount["Type"] not in {"bind", "volume"}:
                raise ValueError("unsupported shared mount")
            source = Path(mount["Source"])
            if not source.is_file() and not source.is_dir():
                raise ValueError("unknown mount endpoint")
            if mount["Type"] == "volume":
                mapped_path(row, mount["Destination"])
            paths.append(("writable-mount" if mount["RW"] else "mount", source))
    private = {
        config["evidence_root"],
        config["seed_root"],
        config["binding_path"],
        *config["journal_roots"],
        "/opt/opencitadel-capacity/observer",
        "/opt/opencitadel-capacity/guest_seal_entry.py",
        "/etc/opencitadel-capacity.json",
        "/etc/opencitadel-capacity-seal.json",
    }
    paths.extend(("private", Path(p)) for p in sorted(private))
    # Check the entire persistent tree for nested external mounts or hidden
    # links. SQL enumerated tablespace/WAL links are the only supported links.
    root_device = Path("/").stat().st_dev
    for role, path in paths:
        if role not in {
            "datastore",
            "wal",
            "tablespace",
            "objects",
            "redis",
            "writers",
            "broker-journal",
            "journals",
            "writable-mount",
        }:
            continue
        if path.absolute() != path.resolve():
            raise ValueError("unmapped persistence symlink in path")
        for entry in chain((path,), path.rglob("*")):
            if entry.is_symlink():
                if entry not in allowed_links:
                    raise ValueError("unmapped persistence symlink")
                continue
            if entry.stat().st_dev != root_device:
                raise ValueError("external nested persistence mount")
    root = mount_readback(Path("/"))
    observed = [
        {"role": role, "path": str(path.resolve()), "mount": mount_readback(path)}
        for role, path in paths
    ]
    return validate_coverage(root, observed, config["disk_serial"])
