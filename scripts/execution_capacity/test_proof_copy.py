"""Descriptor-safe independent private copies; temporary files only."""

import hashlib
import os

import pytest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget


def fixture_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    (source / "originals").mkdir(mode=0o700)
    raw = b"private original bytes"
    path = source / "originals" / "000000.jsonl"
    path.write_bytes(raw)
    path.chmod(0o600)
    files = {
        "originals/000000.jsonl": {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }
    }
    return source, path, raw, files


def test_private_copy_independently_reopens_exact_bytes_and_modes(tmp_path):
    from scripts.execution_capacity.proof_copy import copy_private

    source, path, raw, files = fixture_files(tmp_path)
    destination = tmp_path / "destination"
    copy_private(source, destination, files, budget=EvidenceBudget())
    path.unlink()
    assert (destination / "originals" / "000000.jsonl").read_bytes() == raw
    assert destination.stat().st_mode & 0o777 == 0o700
    assert (destination / "originals").stat().st_mode & 0o777 == 0o700
    assert (destination / "originals" / "000000.jsonl").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("fault", ["symlink", "parent-symlink", "bytes", "quota", "fsync"])
def test_private_copy_never_promotes_partial_or_changed_source(tmp_path, monkeypatch, fault):
    from scripts.execution_capacity.proof_copy import copy_private

    source, path, _raw, files = fixture_files(tmp_path)
    budget = EvidenceBudget()
    if fault == "symlink":
        replacement = source / "other"
        path.rename(replacement)
        path.symlink_to(replacement)
    elif fault == "parent-symlink":
        (source / "originals").rename(source / "other")
        (source / "originals").symlink_to(source / "other")
    elif fault == "bytes":
        path.write_bytes(b"changed original bytes")
    elif fault == "quota":
        budget = EvidenceBudget(bytes_limit=1)
    elif fault == "fsync":
        monkeypatch.setattr(
            os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("fixture sync failure"))
        )
    with pytest.raises((ValueError, OSError)):
        copy_private(source, tmp_path / "destination", files, budget=budget)


@pytest.mark.parametrize("boundary", ["source", "destination"])
def test_private_copy_rejects_fifo_before_io_without_blocking(tmp_path, monkeypatch, boundary):
    from scripts.execution_capacity.proof_copy import copy_private

    source, _path, _raw, files = fixture_files(tmp_path)
    destination = tmp_path / "destination"
    real_open, real_read = os.open, os.read
    member_reads = 0
    fifo_descriptors = []

    def changed_member(path, flags, *args, **kwargs):
        nonlocal member_reads
        if path == "000000.jsonl" and flags & os.O_ACCMODE == os.O_RDONLY:
            member_reads += 1
            if member_reads == (1 if boundary == "source" else 2):
                # Guard before creating/opening a FIFO: old blocking flags fail
                # deterministically rather than letting the regression hang.
                assert flags & os.O_NONBLOCK
                assert flags & os.O_NOFOLLOW
                os.unlink(path, dir_fd=kwargs["dir_fd"])
                os.mkfifo(path, 0o600, dir_fd=kwargs["dir_fd"])
                descriptor = real_open(path, flags, *args, **kwargs)
                fifo_descriptors.append(descriptor)
                return descriptor
        return real_open(path, flags, *args, **kwargs)

    def no_fifo_read(descriptor, size):
        assert descriptor not in fifo_descriptors
        return real_read(descriptor, size)

    monkeypatch.setattr(os, "open", changed_member)
    monkeypatch.setattr(os, "read", no_fifo_read)
    with pytest.raises(ValueError, match="single-link regular file"):
        copy_private(source, destination, files, budget=EvidenceBudget())
    assert len(fifo_descriptors) == 1
    if boundary == "source":
        assert not (destination / "originals" / "000000.jsonl").exists()
    else:
        assert (destination / "originals" / "000000.jsonl").is_fifo()
