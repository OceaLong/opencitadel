"""Real thread lifetime plus private journal; SDK network calls substituted."""

import asyncio
import io
import threading
from types import SimpleNamespace

import pytest
from scripts.execution_capacity import writer_storage as source
from scripts.execution_capacity.observers import RecoveryJournal


class Response(io.BytesIO):
    def release_conn(self):
        pass


class SDK:
    def __init__(self):
        self.started, self.release = threading.Event(), threading.Event()
        self.data = None

    def put_object(self, bucket_name, object_name, data, length, **kwargs):
        self.started.set()
        assert self.release.wait(2)
        self.data = data.read()
        return SimpleNamespace(version_id="actual-version", etag="actual-etag")

    def get_object(self, bucket, key, **kwargs):
        return Response(self.data)


def test_cancelled_await_does_not_complete_sdk_thread_or_clear_attempt(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    sdk = SDK()
    observed = source.Uploads(sdk, "owned", root, "writer-1")

    async def check():
        task = asyncio.create_task(
            asyncio.to_thread(observed.put_object, "owned", "key", io.BytesIO(b"abc"), 3)
        )
        assert await asyncio.to_thread(sdk.started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(TimeoutError):
            await observed.drain(0.01)
        with RecoveryJournal(root) as journal:
            rows = list(journal.records("sdk_upload"))
            assert len(rows) == 1
            assert rows[0][1]["receipt"] is None
        sdk.release.set()
        await observed.drain(2)
        with RecoveryJournal(root) as journal:
            row = next(iter(journal.records("sdk_upload")))[1]
            assert row["receipt"]["size"] == 3
            assert row["receipt"]["version_id"] == "actual-version"

    asyncio.run(check())


def test_prior_unacknowledged_physical_attempt_is_not_recovered_by_new_put(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    with RecoveryJournal(root) as journal:
        journal.intent("sdk_upload", "old", {"writer_id": "writer-1", "key": "key"})
    sdk = SDK()
    sdk.release.set()
    observed = source.Uploads(sdk, "owned", root, "writer-1")
    observed.put_object("owned", "key", io.BytesIO(b"abc"), 3)
    with pytest.raises(ValueError, match="uncertain"):
        asyncio.run(observed.drain(1))


def test_wrong_bucket_is_rejected_before_sdk_and_sealed_entry_rejects_late_work(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    sdk = SDK()
    observed = source.Uploads(sdk, "owned", root, "writer-1")
    with pytest.raises(ValueError, match="unbound"):
        observed.put_object("foreign", "key", io.BytesIO(b"abc"), 3)
    asyncio.run(observed.drain(1, close=True))
    with pytest.raises(ValueError, match="admission closed"):
        observed.put_object("owned", "key", io.BytesIO(b"abc"), 3)
    assert not sdk.started.is_set()


def test_writer_journal_retains_supervisor_timeout_and_unknown_upload(tmp_path, monkeypatch):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(
        source,
        "process_identity",
        lambda: {
            "pid": 17,
            "hostname": "owned",
            "boot_id": "boot",
            "start_ticks": 19,
            "argv_digest": "a" * 64,
        },
    )
    record = source.WriterRecord(
        root, "writer", {"invocation": "attempt", "source_sha256": "b" * 64}
    )
    report = SimpleNamespace(
        name="worker", kind="critical", state="timed_out", attempts=1, error=TimeoutError()
    )
    record.supervisor({"worker": report})
    record.closed()
    with RecoveryJournal(root) as journal:
        writer = journal.get("writer", "writer")
        assert writer["body"]["start_ticks"] == 19
        assert writer["receipt"]["resource_closed"] is True
        stopped = journal.get("writer_supervisor", "writer")
        assert stopped["body"]["reports"][0]["state"] == "timed_out"
        assert stopped["body"]["reports"][0]["error"] == "TimeoutError"


def test_port_and_sdk_attempt_are_linked_without_double_counting(tmp_path):
    from hashlib import sha256
    from uuid import uuid4

    from scripts.execution_capacity.observers import ObservedStorage

    tmp_path.chmod(0o700)
    sdk = SDK()
    sdk.release.set()
    physical = source.Uploads(sdk, "owned", tmp_path, "writer")

    class Port:
        async def put_bytes(self, key, data):
            await asyncio.to_thread(physical.put_object, "owned", key, io.BytesIO(data), len(data))

        async def get_bytes(self, key):
            return sdk.data

    with RecoveryJournal(tmp_path) as journal:
        run = str(uuid4())
        journal.intent("run", run, {"scope": "user:owned"})
        key = f"execution/inputs/{run}/{sha256(b'abc').hexdigest()}.json"
        asyncio.run(ObservedStorage(Port(), journal).put_bytes(key, b"abc"))
        port = list(journal.records("upload"))
        sdk_rows = list(journal.records("sdk_upload"))
        assert len(port) == len(sdk_rows) == 1
        assert sdk_rows[0][1]["body"]["port_upload_id"] == port[0][0]
        assert port[0][1]["receipt"] is not None
