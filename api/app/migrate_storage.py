#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""One-shot object storage migration between COS and MinIO."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import random
import sys
from dataclasses import dataclass
from typing import Literal, Protocol

from starlette.concurrency import run_in_threadpool

from app.infrastructure.logging import setup_logging
from app.infrastructure.storage.cos import Cos
from app.infrastructure.storage.minio import Minio
from app.runtime_role import ProcessRole, set_role

set_role(ProcessRole.MIGRATE)

logger = logging.getLogger(__name__)

Provider = Literal["cos", "minio"]
_PROGRESS_INTERVAL = 50
_OBJECT_MAX_RETRIES = 3
_VERIFY_SAMPLE_SIZE = 20
_LARGE_OBJECT_THRESHOLD_BYTES = 100 * 1024 * 1024  # 100 MB


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int


class StorageBackend(Protocol):
    async def init(self) -> None: ...

    async def shutdown(self) -> None: ...

    async def list_objects(self, prefix: str = "") -> list[ObjectInfo]: ...

    async def get_bytes(self, key: str) -> bytes: ...

    async def put_bytes(self, key: str, data: bytes) -> None: ...

    async def object_size(self, key: str) -> int | None: ...


class CosBackend:
    def __init__(self) -> None:
        self._cos = Cos()

    async def init(self) -> None:
        await self._cos.init()

    async def shutdown(self) -> None:
        await self._cos.shutdown()

    async def list_objects(self, prefix: str = "") -> list[ObjectInfo]:
        results: list[ObjectInfo] = []
        marker = ""
        while True:
            kwargs: dict = {"Bucket": self._cos.bucket, "MaxKeys": 1000}
            if prefix:
                kwargs["Prefix"] = prefix
            if marker:
                kwargs["Marker"] = marker
            response = await run_in_threadpool(self._cos.client.list_objects, **kwargs)
            contents = response.get("Contents") or []
            for item in contents:
                results.append(ObjectInfo(key=item["Key"], size=int(item.get("Size", 0))))
            if not response.get("IsTruncated"):
                break
            marker = response.get("NextMarker") or (results[-1].key if results else "")
            if not marker:
                break
        return results

    async def get_bytes(self, key: str) -> bytes:
        return await self._cos.get_bytes(key)

    async def put_bytes(self, key: str, data: bytes) -> None:
        await self._cos.put_bytes(key, data)

    async def object_size(self, key: str) -> int | None:
        try:
            response = await run_in_threadpool(
                self._cos.client.head_object,
                Bucket=self._cos.bucket,
                Key=key,
            )
            return int(response.get("Content-Length", 0))
        except Exception:
            return None


class MinioBackend:
    def __init__(self) -> None:
        self._minio = Minio()

    async def init(self) -> None:
        await self._minio.init()

    async def shutdown(self) -> None:
        await self._minio.shutdown()

    async def list_objects(self, prefix: str = "") -> list[ObjectInfo]:
        bucket = self._minio.bucket
        objects = await run_in_threadpool(
            lambda: list(
                self._minio.client.list_objects(
                    bucket,
                    prefix=prefix,
                    recursive=True,
                ),
            ),
        )
        return [
            ObjectInfo(key=obj.object_name, size=int(obj.size or 0))
            for obj in objects
        ]

    async def get_bytes(self, key: str) -> bytes:
        return await self._minio.get_bytes(key)

    async def put_bytes(self, key: str, data: bytes) -> None:
        await self._minio.put_bytes(key, data)

    async def object_size(self, key: str) -> int | None:
        try:
            stat = await run_in_threadpool(
                self._minio.client.stat_object,
                self._minio.bucket,
                key,
            )
            return int(stat.size)
        except Exception:
            return None


def create_backend(provider: Provider) -> StorageBackend:
    if provider == "cos":
        return CosBackend()
    return MinioBackend()


async def copy_object(
        source: StorageBackend,
        target: StorageBackend,
        obj: ObjectInfo,
        *,
        dry_run: bool,
) -> str:
    """Copy one object. Returns 'copied', 'skipped', or raises on failure."""
    target_size = await target.object_size(obj.key)
    if target_size is not None and target_size == obj.size:
        return "skipped"

    if dry_run:
        return "copied"

    _LARGE_OBJECT_BYTES = 100 * 1024 * 1024  # 100 MB
    if obj.size > _LARGE_OBJECT_BYTES:
        logger.warning(
            "Large object detected: %s (%s MB). "
            "Increase migration container mem_limit or use --concurrency 1 to reduce peak memory.",
            obj.key,
            obj.size // (1024 * 1024),
        )

    last_exc: Exception | None = None
    for attempt in range(1, _OBJECT_MAX_RETRIES + 1):
        try:
            data = await source.get_bytes(obj.key)
            await target.put_bytes(obj.key, data)
            return "copied"
        except Exception as exc:
            last_exc = exc
            if attempt < _OBJECT_MAX_RETRIES:
                await asyncio.sleep(1.0 * attempt)
    raise RuntimeError(f"failed to copy {obj.key}: {last_exc}") from last_exc


async def migrate_objects(
        source: StorageBackend,
        target: StorageBackend,
        *,
        prefix: str = "",
        dry_run: bool = False,
        concurrency: int = 4,
) -> tuple[int, int, list[str]]:
    await source.init()
    await target.init()
    try:
        objects = await source.list_objects(prefix)
        logger.info("Found %s objects in source bucket (prefix=%r)", len(objects), prefix or "")

        copied = 0
        skipped = 0
        failed: list[str] = []
        sem = asyncio.Semaphore(max(1, concurrency))

        async def _copy_one(obj: ObjectInfo, index: int) -> None:
            nonlocal copied, skipped
            async with sem:
                try:
                    result = await copy_object(source, target, obj, dry_run=dry_run)
                    if result == "copied":
                        copied += 1
                    else:
                        skipped += 1
                    if (index + 1) % _PROGRESS_INTERVAL == 0:
                        logger.info("Progress: %s/%s objects processed", index + 1, len(objects))
                except Exception as exc:
                    logger.error("Copy failed for %s: %s", obj.key, exc)
                    failed.append(obj.key)

        await asyncio.gather(*[_copy_one(obj, i) for i, obj in enumerate(objects)])
        return copied, skipped, failed
    finally:
        await source.shutdown()
        await target.shutdown()


async def verify_migration(
        source: StorageBackend,
        target: StorageBackend,
        *,
        prefix: str = "",
        sample_size: int = _VERIFY_SAMPLE_SIZE,
) -> tuple[list[str], list[str]]:
    """Return (size_mismatches, hash_mismatches)."""
    await source.init()
    await target.init()
    try:
        source_objects = await source.list_objects(prefix)
        target_objects = {obj.key: obj.size for obj in await target.list_objects(prefix)}

        size_mismatches: list[str] = []
        for obj in source_objects:
            target_size = target_objects.get(obj.key)
            if target_size is None:
                size_mismatches.append(f"{obj.key}: missing on target")
            elif target_size != obj.size:
                size_mismatches.append(
                    f"{obj.key}: source={obj.size} target={target_size}",
                )

        extra_on_target = set(target_objects) - {obj.key for obj in source_objects}
        for key in sorted(extra_on_target):
            size_mismatches.append(f"{key}: extra on target")

        hash_mismatches: list[str] = []
        candidates = [
            obj for obj in source_objects
            if obj.key in target_objects and target_objects[obj.key] == obj.size
        ]
        sample = candidates if len(candidates) <= sample_size else random.sample(candidates, sample_size)
        for obj in sample:
            source_hash = hashlib.md5(await source.get_bytes(obj.key)).hexdigest()
            target_hash = hashlib.md5(await target.get_bytes(obj.key)).hexdigest()
            if source_hash != target_hash:
                hash_mismatches.append(f"{obj.key}: md5 mismatch")

        return size_mismatches, hash_mismatches
    finally:
        await source.shutdown()
        await target.shutdown()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate objects between COS and MinIO")
    parser.add_argument("--source", choices=["cos", "minio"], required=True)
    parser.add_argument("--target", choices=["cos", "minio"], required=True)
    parser.add_argument("--prefix", default="", help="Only migrate keys with this prefix")
    parser.add_argument("--dry-run", action="store_true", help="List objects to copy without writing")
    parser.add_argument("--verify-only", action="store_true", help="Verify target matches source")
    parser.add_argument("--concurrency", type=int, default=4)
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    if args.source == args.target:
        logger.error("Source and target must differ")
        return 1

    source = create_backend(args.source)
    target = create_backend(args.target)

    if args.verify_only:
        size_mismatches, hash_mismatches = await verify_migration(
            source,
            target,
            prefix=args.prefix,
        )
        if size_mismatches:
            logger.error("Size mismatches (%s):", len(size_mismatches))
            for item in size_mismatches[:50]:
                logger.error("  %s", item)
        if hash_mismatches:
            logger.error("Hash mismatches (%s):", len(hash_mismatches))
            for item in hash_mismatches:
                logger.error("  %s", item)
        if size_mismatches or hash_mismatches:
            return 1
        logger.info("Verification passed")
        return 0

    copied, skipped, failed = await migrate_objects(
        source,
        target,
        prefix=args.prefix,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
    )
    logger.info(
        "Migration complete: copied=%s skipped=%s failed=%s dry_run=%s",
        copied,
        skipped,
        len(failed),
        args.dry_run,
    )
    if failed:
        logger.error("Failed keys (%s):", len(failed))
        for key in failed[:50]:
            logger.error("  %s", key)
        return 1
    return 0


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    args = parse_args(argv)
    exit_code = asyncio.run(run(args))
    if exit_code != 0:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
