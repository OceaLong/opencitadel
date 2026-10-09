"""Actual descriptor copy of bounded local artifacts, independent of role replay."""

import hashlib
import os

import pytest
from scripts.acceptance.capacity_models import Artifact


def artifact(root, name, data):
    (root / name).write_bytes(data)
    return Artifact(
        path=name,
        role="measurements",
        schema_version=3,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


def test_artifact_copy_uses_bounded_buffers_and_preserves_exact_bytes(tmp_path, monkeypatch):
    from scripts.acceptance.capacity_io import copy_artifacts

    source = tmp_path / "source"
    source.mkdir()
    data = b"binary\x00\xff" * (256 * 1024)
    descriptor = artifact(source, "native.bin", data)
    target = tmp_path / "retained"
    sizes = []
    read = os.read

    def checked_read(fd, size):
        sizes.append(size)
        assert size <= 1024 * 1024
        return read(fd, size)

    monkeypatch.setattr(os, "read", checked_read)
    copy_artifacts([descriptor], source, target)
    assert sizes
    assert (target / "native.bin").read_bytes() == data


def test_copy_rechecks_late_changed_shard_and_keeps_prior_evidence(tmp_path):
    from scripts.acceptance.capacity_io import copy_artifacts

    source = tmp_path / "source"
    source.mkdir()
    descriptors = [artifact(source, "first", b"first"), artifact(source, "last", b"last")]
    (source / "last").write_bytes(b"changed-after-validation")
    target = tmp_path / "retained"
    with pytest.raises(ValueError, match="artifact"):
        copy_artifacts(descriptors, source, target)
    assert (target / "first").read_bytes() == b"first"
    assert not (target / "last").exists()
    assert not (target / "report.json").exists()


def test_copy_requires_fresh_destination_and_does_not_truncate_hardlinks(tmp_path):
    from scripts.acceptance.capacity_io import copy_artifacts

    source = tmp_path / "source"
    source.mkdir()
    descriptor = artifact(source, "sample", b"sample")
    target = tmp_path / "existing"
    target.mkdir()
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"foreign")
    os.link(foreign, target / "sample")
    with pytest.raises((OSError, ValueError)):
        copy_artifacts([descriptor], source, target)
    assert foreign.read_bytes() == b"foreign"


def test_copy_rejects_fifo_before_any_read_without_blocking(tmp_path, monkeypatch):
    from scripts.acceptance.capacity_io import copy_artifacts

    source = tmp_path / "source"
    source.mkdir()
    descriptor = artifact(source, "sample", b"sample")
    original = os.open

    def replace(name, flags, *args, **kwargs):
        if name == "sample" and flags & os.O_CREAT == 0:
            assert flags & os.O_NONBLOCK
            assert flags & os.O_NOFOLLOW
            (source / "sample").unlink()
            os.mkfifo(source / "sample", 0o600)
        return original(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace)
    with pytest.raises(ValueError, match="regular"):
        copy_artifacts([descriptor], source, tmp_path / "retained")


def test_copy_rejects_mutation_during_read(tmp_path, monkeypatch):
    from scripts.acceptance.capacity_io import copy_artifacts

    source = tmp_path / "source"
    source.mkdir()
    descriptor = artifact(source, "sample", b"sample")
    original = os.read
    mutated = False

    def replace(fd, size):
        nonlocal mutated
        data = original(fd, size)
        if not mutated:
            mutated = True
            (source / "sample").write_bytes(b"sample")
            stat = (source / "sample").stat()
            os.utime(source / "sample", ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000000))
        return data

    monkeypatch.setattr(os, "read", replace)
    with pytest.raises(ValueError, match="changed"):
        copy_artifacts([descriptor], source, tmp_path / "retained")
