"""Pure byte-stream framing checks; no launcher or service."""

import struct
import threading

import pytest
from scripts.execution_capacity.native_failed_handoff import HEADER, RECORD, encode_frame
from scripts.execution_capacity.native_failed_stream import NativeFailedHandoffStreamServer
from scripts.execution_capacity.native_raw import _canonical
from scripts.execution_capacity.test_native_failed_handoff import _host


class FragmentedReader:
    def __init__(self, raw: bytes, fragment: int = 3):
        self.raw = raw
        self.fragment = fragment
        self.position = 0
        self.requested = []

    def read(self, size: int) -> bytes:
        self.requested.append(size)
        count = min(size, self.fragment, len(self.raw) - self.position)
        part = self.raw[self.position : self.position + count]
        self.position += count
        return part


class FragmentedWriter:
    def __init__(self, *, short=False):
        self.parts = []
        self.short = short
        self.flushed = 0

    def write(self, raw: bytes) -> int:
        self.parts.append(raw)
        return len(raw) - 1 if self.short else len(raw)

    def flush(self):
        self.flushed += 1


def test_fragmented_requests_and_coalesced_next_request_ack_only_after_durable_row(
    tmp_path, monkeypatch
):
    fixture, key, ledger, native, consumer = _host(tmp_path, monkeypatch)
    try:
        first = _canonical(fixture["records"][0]) + b"\n"
        second = _canonical(fixture["records"][1]) + b"\n"
        reader = FragmentedReader(
            encode_frame(RECORD, 1, key, first) + encode_frame(RECORD, 2, key, second)
        )
        writer = FragmentedWriter()
        server = NativeFailedHandoffStreamServer(consumer, reader, writer)
        server.serve_one()
        assert len(ledger.records("native-record-ack")) == 1
        server.serve_one()
        assert len(ledger.records("native-record-ack")) == 2
        assert writer.flushed == 2
        assert all(size <= 64 * 1024 for size in reader.requested)
        assert all(len(part) <= 64 * 1024 for part in writer.parts)
        assert len(b"".join(writer.parts)) == 2 * (HEADER.size + 4)
    finally:
        native.close()
        ledger.__exit__(None, None, None)


@pytest.mark.parametrize(
    "defect", ["oversized", "wrong-key", "wrong-order", "truncated", "bad-kind"]
)
def test_header_or_payload_defect_poisoned_before_host_write(tmp_path, monkeypatch, defect):
    fixture, key, ledger, native, consumer = _host(tmp_path, monkeypatch)
    try:
        line = _canonical(fixture["records"][0]) + b"\n"
        raw = bytearray(encode_frame(RECORD, 1, key, line))
        if defect == "oversized":
            struct.pack_into(">I", raw, 41, 96 * 1024)
        elif defect == "wrong-key":
            raw[9] ^= 1
        elif defect == "wrong-order":
            struct.pack_into(">I", raw, 5, 2)
        elif defect == "truncated":
            raw = raw[:-1]
        else:
            raw[4] = 4
        reader = FragmentedReader(bytes(raw))
        writer = FragmentedWriter()
        server = NativeFailedHandoffStreamServer(consumer, reader, writer)
        with pytest.raises(ValueError, match="handoff"):
            server.serve_one()
        assert server.poisoned
        assert ledger.records("native-record-ack") == ()
        assert writer.parts == []
        if defect == "oversized":
            assert reader.position == HEADER.size
        with pytest.raises(ValueError, match="closed"):
            server.serve_one()
        next_server = NativeFailedHandoffStreamServer(
            consumer,
            FragmentedReader(encode_frame(RECORD, 1, key, line)),
            FragmentedWriter(),
        )
        with pytest.raises(ValueError, match="closed"):
            next_server.serve_one()
    finally:
        native.close()
        ledger.__exit__(None, None, None)


def test_lost_ack_after_durable_row_keeps_one_uncommitted_prefix(tmp_path, monkeypatch):
    fixture, key, ledger, native, consumer = _host(tmp_path, monkeypatch)
    try:
        line = _canonical(fixture["records"][0]) + b"\n"
        reader = FragmentedReader(encode_frame(RECORD, 1, key, line))
        writer = FragmentedWriter(short=True)
        server = NativeFailedHandoffStreamServer(consumer, reader, writer)
        with pytest.raises(ValueError, match="uncertain short write"):
            server.serve_one()
        assert server.poisoned
        assert consumer.poisoned
        assert len(ledger.records("native-record-ack")) == 1
        assert ledger.records("native-failure-close") == ()
        assert len(writer.parts) == 1
        with pytest.raises(ValueError, match="closed"):
            server.serve_one()
        next_server = NativeFailedHandoffStreamServer(
            consumer,
            FragmentedReader(encode_frame(RECORD, 2, key, line)),
            FragmentedWriter(),
        )
        with pytest.raises(ValueError, match="closed"):
            next_server.serve_one()
    finally:
        native.close()
        ledger.__exit__(None, None, None)


def test_second_server_cannot_consume_while_first_ack_is_uncertain(tmp_path, monkeypatch):
    fixture, key, ledger, native, consumer = _host(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    second_lock_attempted = threading.Event()
    errors = []

    class TrackingLock:
        def __init__(self, lock):
            self.lock = lock
            self.second_probe_done = False

        def __enter__(self):
            if threading.current_thread().name == "second-handoff" and not self.second_probe_done:
                if self.lock.acquire(blocking=False):
                    self.lock.release()
                    raise AssertionError("first ACK did not hold shared consumer lock")
                self.second_probe_done = True
                second_lock_attempted.set()
            self.lock.acquire()
            return self

        def __exit__(self, *_):
            self.lock.release()

    consumer._lock = TrackingLock(consumer._lock)

    class BlockingShortWriter(FragmentedWriter):
        def write(self, raw: bytes) -> int:
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test ACK release missing")
            return super().write(raw)

    try:
        first = _canonical(fixture["records"][0]) + b"\n"
        second = _canonical(fixture["records"][1]) + b"\n"
        first_server = NativeFailedHandoffStreamServer(
            consumer,
            FragmentedReader(encode_frame(RECORD, 1, key, first)),
            BlockingShortWriter(short=True),
        )
        second_reader = FragmentedReader(encode_frame(RECORD, 2, key, second))
        second_server = NativeFailedHandoffStreamServer(consumer, second_reader, FragmentedWriter())

        def serve(server):
            try:
                server.serve_one()
            except (ValueError, AssertionError) as error:
                errors.append(type(error).__name__)

        first_thread = threading.Thread(target=serve, args=(first_server,))
        second_thread = threading.Thread(target=serve, args=(second_server,), name="second-handoff")
        first_thread.start()
        assert entered.wait(timeout=5)
        second_thread.start()
        assert second_lock_attempted.wait(timeout=5)
        assert len(ledger.records("native-record-ack")) == 1
        assert second_reader.position == 0
        release.set()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)
        assert not first_thread.is_alive()
        assert not second_thread.is_alive()
        assert errors == ["ValueError", "ValueError"]
        assert consumer.poisoned
        assert len(ledger.records("native-record-ack")) == 1
        assert second_reader.position == 0
    finally:
        release.set()
        if "first_thread" in locals() and first_thread.ident is not None:
            first_thread.join(timeout=5)
        if "second_thread" in locals() and second_thread.ident is not None:
            second_thread.join(timeout=5)
        native.close()
        ledger.__exit__(None, None, None)
