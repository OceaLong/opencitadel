"""Minio real-test opt-in exercises SDK-shaped boundaries without a network."""

import asyncio
from types import SimpleNamespace

import pytest

from app.infrastructure.storage.minio import Minio


def settings(env="test"):
    return SimpleNamespace(
        env=env,
        evaluation_acceptance_enabled=False,
        minio_endpoint="owned-minio:9000",
        minio_bucket="capacity",
        minio_access_key="unit",
        minio_secret_key="unit",
        minio_secure=False,
        minio_public_endpoint="",
    )


def test_default_test_constructor_keeps_no_sdk_behavior(monkeypatch):
    monkeypatch.setattr(
        "app.infrastructure.storage.minio.MinioClient",
        lambda *args, **kwargs: pytest.fail("default test must not contact SDK"),
    )
    client = Minio(settings())
    asyncio.run(client.init())
    assert asyncio.run(client.presigned_get_url("key")) == "https://example.com/key"


@pytest.mark.parametrize("present", [True, False])
def test_explicit_real_test_checks_existing_bucket_and_preserves_real_put(present, monkeypatch):
    calls = []

    class SDK:
        def bucket_exists(self, bucket):
            calls.append(("exists", bucket))
            return present

        def make_bucket(self, *args):
            pytest.fail("no implicit bucket creation")

        def put_object(self, bucket, key, stream, *, length):
            calls.append(("put", bucket, key, stream.read(), length))

    sdk = SDK()
    monkeypatch.setattr("app.infrastructure.storage.minio.MinioClient", lambda *args, **kwargs: sdk)

    async def threadpool(call, *args, **kwargs):
        return call(*args, **kwargs)

    monkeypatch.setattr("app.infrastructure.storage.minio.run_in_threadpool", threadpool)
    client = Minio(settings(), real_test_io=True)
    if present:
        asyncio.run(client.init())
        asyncio.run(client.put_bytes("owned", b"actual"))
        assert calls == [("exists", "capacity"), ("put", "capacity", "owned", b"actual", 6)]
        assert asyncio.run(client.presigned_get_url("owned")) is None
    else:
        with pytest.raises(ValueError, match="preprovisioned"):
            asyncio.run(client.init())
        assert calls == [("exists", "capacity")]


def test_real_test_flag_cannot_change_non_test_mode():
    with pytest.raises(ValueError, match="test"):
        Minio(settings("production"), real_test_io=True)


def test_real_test_preserves_transport_error(monkeypatch):
    error = OSError("unit transport loss")

    class SDK:
        def bucket_exists(self, _bucket):
            raise error

    monkeypatch.setattr(
        "app.infrastructure.storage.minio.MinioClient", lambda *args, **kwargs: SDK()
    )

    async def threadpool(call, *args, **kwargs):
        return call(*args, **kwargs)

    monkeypatch.setattr("app.infrastructure.storage.minio.run_in_threadpool", threadpool)
    with pytest.raises(OSError, match="unit transport loss") as caught:
        asyncio.run(Minio(settings(), real_test_io=True).init())
    assert caught.value is error
