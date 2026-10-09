"""Hash exactly the immutable spooled bytes sent to either storage adapter."""

import hashlib
from contextlib import contextmanager
from tempfile import SpooledTemporaryFile


@contextmanager
def immutable_upload(stream):
    digest = hashlib.sha256()
    size = 0
    with SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as prepared:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            prepared.write(chunk)
        prepared.seek(0)
        yield prepared, digest.hexdigest(), size
