"""Exact private companion copying. Only the proof owner supplies file membership."""

import os
import stat
from contextlib import closing, contextmanager, nullcontext, suppress
from hashlib import sha256
from pathlib import PurePosixPath
from types import GeneratorType


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _private(info, mode):
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != mode:
        raise ValueError("private proof ownership or mode differs")


@contextmanager
def directory(path):
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("absolute private proof directory required")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        _private(os.fstat(descriptor), 0o700)
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def parent(root, relative, *, create=False):
    value = PurePosixPath(relative)
    if (
        value.is_absolute()
        or not value.parts
        or any(part in {"", ".", ".."} for part in value.parts)
        or value.as_posix() != relative
    ):
        raise ValueError("closed relative private proof member required")
    descriptor = os.dup(root)
    try:
        for part in value.parts[:-1]:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            _private(os.fstat(child), 0o700)
            if create:
                os.fsync(descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor, value.name
    finally:
        os.close(descriptor)


def _regular(descriptor):
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("private proof member must be a single-link regular file")
    _private(info, 0o600)
    return info


def _stream(descriptor, expected, *, budget, output=None):
    before = _regular(descriptor)
    if (
        set(expected) != {"sha256", "size_bytes"}
        or type(expected["size_bytes"]) is not int
        or before.st_size != expected["size_bytes"]
    ):
        raise ValueError("private proof member size differs")
    # Charge both transfer/copy working windows and bytes before reading.
    budget.reserve(before.st_size + 2 * min(before.st_size + 1, 65536), rows=1)
    hashed, length = sha256(), 0
    while length < before.st_size:
        raw = os.read(descriptor, min(65536, before.st_size - length))
        if not raw:
            raise ValueError("private proof member truncated")
        hashed.update(raw)
        length += len(raw)
        if output is not None:
            offset = 0
            while offset < len(raw):
                wrote = os.write(output, raw[offset:])
                if wrote <= 0:
                    raise OSError("private proof copy made no progress")
                offset += wrote
    if (
        os.read(descriptor, 1)
        or _identity(before) != _identity(_regular(descriptor))
        or hashed.hexdigest() != expected["sha256"]
    ):
        raise ValueError("private proof original bytes changed")
    return before


def copy_private(source, destination, files, *, budget):
    """Copy exact verified members, retaining partial files on any failure.

    No success marker or authority is emitted. The owning context must reopen
    the destination and replay it independently before issuing any receipt.
    """
    if type(files) is dict:
        member_scope = nullcontext(files.items())
    elif type(files) is GeneratorType:
        member_scope = closing(files)
    else:
        raise TypeError("concrete private member inventory required")
    with directory(destination.parent) as target_parent:
        os.mkdir(destination.name, mode=0o700, dir_fd=target_parent)
        os.fsync(target_parent)
    with (
        member_scope as members,
        directory(source) as source_fd,
        directory(destination) as destination_fd,
    ):
        for relative, expected in members:
            budget.reserve(256, rows=1)
            with (
                parent(source_fd, relative) as (src_parent, name),
                parent(destination_fd, relative, create=True) as (dst_parent, target),
            ):
                original = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=src_parent
                )
                try:
                    _regular(original)
                    copied = os.open(
                        target,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=dst_parent,
                    )
                    try:
                        before = _stream(original, expected, budget=budget, output=copied)
                        os.fsync(copied)
                    finally:
                        os.close(copied)
                    if _identity(before) != _identity(
                        os.stat(name, dir_fd=src_parent, follow_symlinks=False)
                    ):
                        raise ValueError("private proof original path changed")
                finally:
                    os.close(original)
                reopened = os.open(
                    target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dst_parent
                )
                try:
                    _stream(reopened, expected, budget=budget)
                finally:
                    os.close(reopened)
                os.fsync(dst_parent)
        os.fsync(destination_fd)
    with directory(destination.parent) as target_parent:
        os.fsync(target_parent)


def private_container(root, name):
    """Create the fixed private companion directory under an existing runner root."""
    from scripts.acceptance.capacity_io import _directory

    if name != "capacity-private":
        raise ValueError("fixed private companion container required")
    descriptor = _directory(root)
    try:
        os.mkdir(name, mode=0o700, dir_fd=descriptor)
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
        try:
            _private(os.fstat(child), 0o700)
            os.fsync(child)
        finally:
            os.close(child)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return root / name
