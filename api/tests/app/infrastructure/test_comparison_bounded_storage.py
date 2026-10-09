"""Storage budget reaches actual SDK response reads and closes on every exit."""

from io import BytesIO
from types import SimpleNamespace

import pytest

from app.infrastructure.adapters.object_storage import (
    CosObjectStorageAdapter,
    MinioObjectStorageAdapter,
)

pytestmark = pytest.mark.asyncio


class Response(BytesIO):
    released = False

    def __init__(self, value):
        super().__init__(value)
        self.requested = []

    def read(self, size=-1):
        assert 0 < size <= 65537, "whole-object read is forbidden"
        self.requested.append(size)
        return super().read(size)

    def release_conn(self):
        self.released = True


@pytest.mark.parametrize("provider", ["minio", "cos"])
async def test_bounded_storage_limits_input_and_closes_sdk_body(provider):
    response = Response(b"a" * 100000)
    client = SimpleNamespace(
        get_object=lambda *args, **kwargs: response if provider == "minio" else {"Body": response}
    )
    storage = SimpleNamespace(client=client, bucket="bucket")
    adapter = (
        MinioObjectStorageAdapter(storage)
        if provider == "minio"
        else CosObjectStorageAdapter(storage)
    )
    assert hasattr(adapter, "get_bounded_bytes"), "bounded immutable object reader missing"
    result = await adapter.get_bounded_bytes("fixed-key", 65536)
    assert result.data == b"a" * 65536
    assert result.truncated is True
    assert response.closed
    if provider == "minio":
        assert response.released


@pytest.mark.parametrize("provider", ["minio", "cos"])
async def test_bounded_reader_distinguishes_exact_eof_and_preserves_read_failure(provider):
    response = Response(b"exact")
    client = SimpleNamespace(
        get_object=lambda *args, **kwargs: response if provider == "minio" else {"Body": response}
    )
    storage = SimpleNamespace(client=client, bucket="bucket")
    adapter = (
        MinioObjectStorageAdapter(storage)
        if provider == "minio"
        else CosObjectStorageAdapter(storage)
    )
    assert hasattr(adapter, "get_bounded_bytes"), "bounded immutable object reader missing"
    result = await adapter.get_bounded_bytes("key", 5)
    assert result.data == b"exact"
    assert result.truncated is False


@pytest.mark.parametrize("provider", ["minio", "cos"])
async def test_read_error_closes_the_actual_sdk_stream(provider):
    class Broken(Response):
        def read(self, size=-1):
            raise OSError("range transport failed")

    response = Broken(b"")
    client = SimpleNamespace(
        get_object=lambda *args, **kwargs: response if provider == "minio" else {"Body": response}
    )
    storage = SimpleNamespace(client=client, bucket="bucket")
    adapter = (
        MinioObjectStorageAdapter(storage)
        if provider == "minio"
        else CosObjectStorageAdapter(storage)
    )
    with pytest.raises(OSError, match="range transport failed"):
        await adapter.get_bounded_bytes("fixed-key", 100)
    assert response.closed
    assert response.released


async def test_cos_stream_body_uses_raw_stream_without_whole_body_getter():
    response = Response(b"abcdef")
    body = SimpleNamespace(get_raw_stream=lambda: response)
    adapter = CosObjectStorageAdapter(
        SimpleNamespace(
            bucket="bucket", client=SimpleNamespace(get_object=lambda **kwargs: {"Body": body})
        )
    )
    result = await adapter.get_bounded_bytes("key", 3)
    assert result.data == b"abc"
    assert result.truncated
    assert response.closed
