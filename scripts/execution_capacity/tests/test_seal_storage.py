"""Reject declared-only persistence and incomplete used-build byte inventories."""

import io
import tarfile
from pathlib import Path

import pytest


def test_root_coverage_rejects_external_split_and_alias():
    from scripts.execution_capacity.seal_storage import validate_coverage

    root = {
        "major_minor": "8:1",
        "fstype": "ext4",
        "source": "/dev/sda1",
        "disk": "sda",
        "serial": "root-uuid",
    }
    paths = [
        {"role": role, "path": path, "mount": {**root}}
        for role, path in (
            ("os", "/"),
            ("datastore", "/pg"),
            ("wal", "/pg/pg_wal"),
            ("objects", "/minio"),
            ("redis", "/redis"),
            ("writers", "/private/writers"),
            ("docker", "/var/lib/docker"),
            ("source", "/capacity"),
        )
    ]
    assert validate_coverage(root, paths, "root-uuid")["root"]["disk"] == "sda"
    for mutation in (
        {"fstype": "nfs"},
        {"major_minor": "8:2"},
        {"disk": "sdb"},
        {"serial": "foreign"},
    ):
        bad = [
            {**p, "mount": {**p["mount"], **mutation}} if p["role"] == "writers" else p
            for p in paths
        ]
        with pytest.raises(ValueError, match=r"external|root"):
            validate_coverage(root, bad, "root-uuid")
    with pytest.raises(ValueError, match=r"missing|complete"):
        validate_coverage(root, paths[:-1], "root-uuid")


def archive(tmp_path, names):
    path = tmp_path / "root.tar"
    with tarfile.open(path, "w") as out:
        for name, raw in names.items():
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            out.addfile(item, io.BytesIO(raw))
    return path


def test_build_reader_enumerates_entire_actual_export(tmp_path):
    from scripts.execution_capacity.seal_build import read_export

    path = archive(
        tmp_path,
        {
            "app/server.js": b"server",
            "app/.next/static/a.js": b"static",
            "app/node_modules/next/a.js": b"runtime",
            "usr/lib/libc.so": b"native",
        },
    )
    first = read_export(path, "frontend")
    assert first["files"] == 4
    path = archive(
        tmp_path,
        {
            "app/server.js": b"server",
            "app/.next/static/a.js": b"static",
            "app/node_modules/next/a.js": b"runtime",
            "usr/lib/libc.so": b"changed native",
        },
    )
    assert read_export(path, "frontend")["digest"] != first["digest"]
    path = archive(tmp_path, {"app/server.js": b"server"})
    with pytest.raises(ValueError, match="frontend"):
        read_export(path, "frontend")


def test_build_mount_tree_is_exhaustive_and_rejects_external_links(tmp_path):
    from scripts.execution_capacity.seal_build import read_tree

    (tmp_path / "a").write_text("a")
    first = read_tree(tmp_path)
    (tmp_path / "new-dependency").write_text("installed")
    assert read_tree(tmp_path)["digest"] != first["digest"]
    (tmp_path / "link").symlink_to(Path("/outside"))
    with pytest.raises(ValueError, match="link"):
        read_tree(tmp_path)
