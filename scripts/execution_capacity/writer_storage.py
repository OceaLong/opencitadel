"""Capacity-only observation at the single actual SDK PUT boundary.

Both Minio.put_bytes and direct MinioFileStorage calls traverse this proxy. The
synchronous method runs until the real SDK and GET finish, even if its asyncio
caller is cancelled. Each thread opens its own private journal connection.
"""

import asyncio
import threading
import time
from hashlib import sha256
from uuid import uuid4

from scripts.execution_capacity.observers import PORT_UPLOAD, RecoveryJournal

from app.infrastructure.storage.minio import Minio


class Uploads:
    def __init__(self, client, bucket, root, writer_id):
        self.client, self.bucket, self.root, self.writer_id = client, bucket, root, writer_id
        self.condition = threading.Condition()
        self.pending = set()
        self.closed = False

    def __getattr__(self, name):
        return getattr(self.client, name)

    def put_object(self, bucket_name, object_name, data, length, **kwargs):
        if bucket_name != self.bucket or not object_name or length < 0:
            raise ValueError("unbound SDK upload")
        identity = str(uuid4())
        with self.condition:
            if self.closed:
                raise ValueError("writer upload admission closed")
            self.pending.add(identity)
        try:
            # Actual product input/file adapters provide immutable seekable data.
            # Hash with bounded buffers and restore the exact caller position.
            start = data.tell()
            digest, size = sha256(), 0
            while chunk := data.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
            data.seek(start)
            if size != length:
                raise ValueError("SDK upload length differs from immutable bytes")
            body = {
                "port_upload_id": PORT_UPLOAD.get(),
                "writer_id": self.writer_id,
                "key": object_name,
                "bucket": bucket_name,
                "size": size,
                "sha256": digest.hexdigest(),
                "started_ns": time.monotonic_ns(),
            }
            with RecoveryJournal(self.root) as journal:
                journal.intent("sdk_upload", identity, body)
                actual = self.client.put_object(bucket_name, object_name, data, length, **kwargs)
                # Bind GET to returned physical version where the store supports it.
                version_id = getattr(actual, "version_id", None)
                options = {"version_id": version_id} if version_id is not None else {}
                response = self.client.get_object(bucket_name, object_name, **options)
                read_digest, read_size = sha256(), 0
                try:
                    while chunk := response.read(1024 * 1024):
                        read_digest.update(chunk)
                        read_size += len(chunk)
                        if read_size > size:
                            raise ValueError("SDK GET changed upload size")
                finally:
                    response.close()
                    response.release_conn()
                if read_size != size or read_digest.hexdigest() != digest.hexdigest():
                    raise ValueError("SDK upload readback differs")
                journal.acknowledge(
                    "sdk_upload",
                    identity,
                    {
                        "size": size,
                        "sha256": digest.hexdigest(),
                        "version_id": version_id,
                        "etag": getattr(actual, "etag", None),
                        "completed_ns": time.monotonic_ns(),
                    },
                )
                return actual
        finally:
            with self.condition:
                self.pending.remove(identity)
                self.condition.notify_all()

    async def drain(self, budget_seconds=30, *, close=False):
        def wait():
            with self.condition:
                if close:
                    self.closed = True
                if not self.condition.wait_for(lambda: not self.pending, timeout=budget_seconds):
                    raise TimeoutError("actual SDK upload thread remains active")
            with RecoveryJournal(self.root) as journal:
                if any(
                    row["body"]["writer_id"] == self.writer_id and row["receipt"] is None
                    for _, row in journal.records("sdk_upload")
                ):
                    raise ValueError("original SDK upload remains uncertain")

        await asyncio.to_thread(wait)


class ObservedMinio(Minio):
    def __init__(self, settings, *, root, writer_id, binding):
        super().__init__(settings, real_test_io=True)
        self.observation_root, self.writer_id = root, writer_id
        self.uploads = None
        self.binding = binding
        self.record = None

    async def init(self):
        self.record = WriterRecord(self.observation_root, self.writer_id, self.binding)
        await super().init()
        self.uploads = Uploads(self._client, self.bucket, self.observation_root, self.writer_id)
        self._client = self.uploads

    async def shutdown(self):
        # Client references survive a failed drain for exact recovery. This is
        # still not evidence that the owning process/container actually exited.
        if self.uploads is not None:
            await self.uploads.drain(close=True)
        await super().shutdown()
        if self.record is not None:
            self.record.closed()


def process_identity():
    import os
    import socket
    from pathlib import Path

    pid = os.getpid()
    stat = Path("/proc/self/stat").read_text()
    return {
        "pid": pid,
        "hostname": socket.gethostname(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "start_ticks": int(stat[stat.rfind(")") + 2 :].split()[19]),
        "pid_namespace": Path("/proc/self/ns/pid").stat().st_ino,
        "argv_digest": sha256(Path("/proc/self/cmdline").read_bytes()).hexdigest(),
    }


class WriterRecord:
    def __init__(self, root, identity, binding):
        self.root, self.identity = root, identity
        with RecoveryJournal(root) as journal:
            journal.intent(
                "writer",
                identity,
                {
                    **process_identity(),
                    "invocation": binding["invocation"],
                    "source_sha256": binding["source_sha256"],
                    "started_ns": time.monotonic_ns(),
                },
            )

    def supervisor(self, reports):
        with RecoveryJournal(self.root) as journal:
            journal.intent(
                "writer_supervisor",
                self.identity,
                {
                    "reports": [
                        {
                            "name": row.name,
                            "kind": str(row.kind),
                            "state": str(row.state),
                            "attempts": row.attempts,
                            "error": None if row.error is None else type(row.error).__name__,
                        }
                        for row in reports.values()
                    ],
                    "observed_ns": time.monotonic_ns(),
                },
            )

    def closed(self):
        with RecoveryJournal(self.root) as journal:
            journal.acknowledge(
                "writer",
                self.identity,
                {"resource_closed": True, "observed_ns": time.monotonic_ns()},
            )
