#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

from app.migrate_storage import ObjectInfo, copy_object, migrate_objects, verify_migration


class FakeBackend:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self._objects: dict[str, bytes] = dict(objects or {})
        self.put_calls: list[tuple[str, bytes]] = []

    async def init(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def list_objects(self, prefix: str = "") -> list[ObjectInfo]:
        keys = sorted(k for k in self._objects if k.startswith(prefix))
        return [ObjectInfo(key=k, size=len(self._objects[k])) for k in keys]

    async def get_bytes(self, key: str) -> bytes:
        return self._objects[key]

    async def put_bytes(self, key: str, data: bytes) -> None:
        self.put_calls.append((key, data))
        self._objects[key] = data

    async def object_size(self, key: str) -> int | None:
        if key not in self._objects:
            return None
        return len(self._objects[key])


def test_migrate_objects_copies_all():
    source = FakeBackend({"a.txt": b"hello", "b.txt": b"world"})
    target = FakeBackend()

    copied, skipped, failed = asyncio.run(migrate_objects(source, target, concurrency=2))

    assert copied == 2
    assert skipped == 0
    assert failed == []
    assert target._objects == {"a.txt": b"hello", "b.txt": b"world"}


def test_migrate_objects_skips_existing_same_size():
    source = FakeBackend({"a.txt": b"hello"})
    target = FakeBackend({"a.txt": b"hello"})

    copied, skipped, failed = asyncio.run(migrate_objects(source, target))

    assert copied == 0
    assert skipped == 1
    assert failed == []
    assert target.put_calls == []


def test_migrate_objects_prefix_filter():
    source = FakeBackend({"logs/a.txt": b"1", "other/b.txt": b"2"})
    target = FakeBackend()

    copied, skipped, failed = asyncio.run(migrate_objects(source, target, prefix="logs/"))

    assert copied == 1
    assert failed == []
    assert "logs/a.txt" in target._objects
    assert "other/b.txt" not in target._objects


def test_copy_object_retries_then_succeeds():
    source = FakeBackend({"k": b"data"})
    target = FakeBackend()
    attempts = {"count": 0}
    original_put = target.put_bytes

    async def patched_put(key: str, data: bytes) -> None:
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise RuntimeError("temporary")
        await original_put(key, data)

    target.put_bytes = patched_put  # type: ignore[method-assign]

    result = asyncio.run(copy_object(source, target, ObjectInfo(key="k", size=4), dry_run=False))
    assert result == "copied"
    assert attempts["count"] == 2
    assert target._objects["k"] == b"data"


def test_migrate_objects_records_failures():
    source = FakeBackend({"bad.txt": b"x"})
    target = FakeBackend()

    async def always_fail(_key: str, _data: bytes) -> None:
        raise RuntimeError("permanent")

    target.put_bytes = always_fail  # type: ignore[method-assign]

    copied, skipped, failed = asyncio.run(migrate_objects(source, target))

    assert copied == 0
    assert skipped == 0
    assert failed == ["bad.txt"]


def test_verify_migration_detects_size_mismatch():
    source = FakeBackend({"a.txt": b"hello"})
    target = FakeBackend({"a.txt": b"hell"})

    size_mismatches, hash_mismatches = asyncio.run(
        verify_migration(source, target, sample_size=1),
    )

    assert any("a.txt" in m for m in size_mismatches)
    assert hash_mismatches == []


def test_verify_migration_detects_hash_mismatch():
    source = FakeBackend({"a.txt": b"hello"})
    target = FakeBackend({"a.txt": b"world"})

    size_mismatches, hash_mismatches = asyncio.run(
        verify_migration(source, target, sample_size=1),
    )

    assert size_mismatches == []
    assert any("a.txt" in m for m in hash_mismatches)


def test_verify_migration_passes_when_identical():
    source = FakeBackend({"a.txt": b"hello", "b.txt": b"world"})
    target = FakeBackend({"a.txt": b"hello", "b.txt": b"world"})

    size_mismatches, hash_mismatches = asyncio.run(verify_migration(source, target))

    assert size_mismatches == []
    assert hash_mismatches == []
