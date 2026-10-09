"""Bounded framed server on caller-owned byte streams; no process authority.

The caller owns stream lifetime, deadlines, preregistration, and teardown. A
generic blocking read/write cannot be cancelled safely by this adapter.
"""

import threading
from typing import Protocol

from scripts.execution_capacity.native_failed_handoff import (
    ACK,
    CALLBACK,
    HEADER,
    MAGIC,
    MAX_ACK,
    MAX_CALLBACK,
    MAX_SNAPSHOT,
    RECORD,
    TERMINAL,
    NativeFailedHandoffConsumer,
)
from scripts.execution_capacity.native_raw import LINE_BYTES

CHUNK = 64 * 1024


class ByteReader(Protocol):
    def read(self, size: int) -> bytes: ...


class ByteWriter(Protocol):
    def write(self, data: bytes) -> int: ...


def _read_exact(reader: ByteReader, size: int) -> bytes:
    result = bytearray(size)
    offset = 0
    while offset < size:
        requested = min(CHUNK, size - offset)
        part = reader.read(requested)
        if type(part) is not bytes or not 0 < len(part) <= requested:
            raise ValueError("handoff stream EOF or overread")
        result[offset : offset + len(part)] = part
        offset += len(part)
    return bytes(result)


def _write_exact(writer: ByteWriter, raw: bytes) -> None:
    for offset in range(0, len(raw), CHUNK):
        part = raw[offset : offset + CHUNK]
        if writer.write(part) != len(part):
            raise ValueError("handoff stream uncertain short write")
    flush = getattr(writer, "flush", None)
    if flush is not None:
        flush()


def _cap(kind: int) -> int:
    if kind == RECORD:
        return LINE_BYTES - 1
    if kind == CALLBACK:
        return MAX_CALLBACK
    if kind == TERMINAL:
        return MAX_SNAPSHOT
    if kind == ACK:
        return MAX_ACK
    raise ValueError("invalid handoff stream kind")


class NativeFailedHandoffStreamServer:
    """One request at a time; ACK follows the consumer's durable host write."""

    def __init__(
        self, consumer: NativeFailedHandoffConsumer, reader: ByteReader, writer: ByteWriter
    ) -> None:
        if type(consumer) is not NativeFailedHandoffConsumer:
            raise TypeError("actual handoff consumer required")
        self.consumer = consumer
        self.reader = reader
        self.writer = writer
        self.poisoned = False
        self._lock = threading.RLock()

    def serve_one(self) -> None:
        # The consumer lock spans read, durable consume, and ACK write. A
        # second adapter cannot advance the next sequence during uncertain ACK.
        with self._lock, self.consumer._lock:
            if self.poisoned or self.consumer.poisoned or self.consumer.terminal_bytes is not None:
                raise ValueError("handoff stream closed or uncertain")
            try:
                header = _read_exact(self.reader, HEADER.size)
                magic, kind, sequence, key, size = HEADER.unpack(header)
                if (
                    magic != MAGIC
                    or kind not in (RECORD, CALLBACK, TERMINAL)
                    or key != bytes.fromhex(self.consumer.native.key)
                    or sequence != self.consumer.sequence + 1
                    or not 0 < size <= _cap(kind)
                ):
                    raise ValueError("invalid handoff stream header")
                payload = _read_exact(self.reader, size)
                acknowledgement = self.consumer.consume(header + payload)
                _write_exact(self.writer, acknowledgement)
            except BaseException:
                self.poisoned = True
                # A complete host append may precede a lost or short ACK.
                # Do not let any adapter continue this uncertain consumer.
                self.consumer.poisoned = True
                raise
