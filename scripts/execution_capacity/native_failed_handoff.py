"""One-command, in-memory failed-evidence frame consumer.

The caller owns the process/stream and the already registered host writers. A
frame contains no path or provenance claim. An uncertain exchange is never
retried in this consumer; the retained host prefix needs independent replay.
"""

import hashlib
import struct
import threading

from scripts.acceptance.capacity_io import strict_json
from scripts.execution_capacity.native_failure_transport import (
    NativeFailureDrainWriter,
    _parse_failure_snapshot_v2,
)
from scripts.execution_capacity.native_raw import IDENTITY, LINE_BYTES, _canonical
from scripts.execution_capacity.native_transport import NativeHostWriter

MAGIC = b"OC3B"
HEADER = struct.Struct(">4sB I 32s I")
RECORD = 1
CALLBACK = 2
TERMINAL = 3
ACK = 4
MAX_SNAPSHOT = 8 * 1024 * 1024
MAX_CALLBACK = LINE_BYTES + 49152
MAX_ACK = 16 * 1024


def _key_bytes(key: str) -> bytes:
    if type(key) is not str or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("exact lower-case command digest required")
    try:
        return bytes.fromhex(key)
    except ValueError as exc:
        raise ValueError("exact lower-case command digest required") from exc


def encode_frame(kind: int, sequence: int, key: str, payload: bytes) -> bytes:
    if kind not in (RECORD, CALLBACK, TERMINAL, ACK):
        raise ValueError("unknown handoff frame kind")
    if type(sequence) is not int or not 0 < sequence <= 0xFFFFFFFF:
        raise ValueError("invalid handoff sequence")
    if type(payload) is not bytes or len(payload) > MAX_SNAPSHOT:
        raise ValueError("handoff payload byte bound differs")
    return HEADER.pack(MAGIC, kind, sequence, _key_bytes(key), len(payload)) + payload


def decode_frame(raw: bytes) -> tuple[int, int, str, bytes]:
    if type(raw) is not bytes or len(raw) < HEADER.size:
        raise ValueError("truncated handoff header")
    magic, kind, sequence, key, size = HEADER.unpack_from(raw)
    if magic != MAGIC or kind not in (RECORD, CALLBACK, TERMINAL, ACK):
        raise ValueError("invalid handoff frame header")
    if sequence == 0 or size > MAX_SNAPSHOT or len(raw) != HEADER.size + size:
        raise ValueError("truncated or oversized handoff frame")
    return kind, sequence, key.hex(), raw[HEADER.size :]


class NativeFailedHandoffConsumer:
    """Bounded frame dispatch to actual durable writers; no close/publication."""

    def __init__(self, native: NativeHostWriter):
        if type(native) is not NativeHostWriter:
            raise TypeError("actual native writer required")
        self.native = native
        self.drain: NativeFailureDrainWriter | None = None
        self.sequence = 0
        self.terminal_bytes: bytes | None = None
        self.poisoned = False
        self._lock = threading.RLock()

    def bind_failure_drain(self, drain: NativeFailureDrainWriter) -> None:
        with self._lock:
            self._bind_failure_drain_locked(drain)

    def _bind_failure_drain_locked(self, drain: NativeFailureDrainWriter) -> None:
        if self.poisoned or self.terminal_bytes is not None or self.drain is not None:
            raise ValueError("handoff drain already bound or uncertain")
        if (
            type(drain) is not NativeFailureDrainWriter
            or drain.native is not self.native
            or drain.wire_version != 2
        ):
            raise ValueError("same-command wire2 failure writer required")
        self.drain = drain

    def consume(self, raw: bytes) -> bytes:
        with self._lock:
            return self._consume_locked(raw)

    def _consume_locked(self, raw: bytes) -> bytes:
        if self.poisoned or self.terminal_bytes is not None:
            raise ValueError("handoff closed or uncertain")
        try:
            kind, sequence, key, payload = decode_frame(raw)
            if kind == ACK or key != self.native.key or sequence != self.sequence + 1:
                raise ValueError("foreign or duplicate handoff frame")
            if kind == RECORD:
                if self.drain is not None:
                    raise ValueError("record frame after failure drain opened")
                if not 1 < len(payload) < LINE_BYTES:
                    raise ValueError("record frame byte bound differs")
                acknowledged = self.native.append_record(payload)
                receipt = struct.pack(">I", acknowledged)
            elif kind == CALLBACK:
                if self.drain is None:
                    raise ValueError("failure drain not bound")
                if not 4 < len(payload) <= MAX_CALLBACK:
                    raise ValueError("callback frame byte bound differs")
                metadata_size = struct.unpack_from(">I", payload)[0]
                if not 0 < metadata_size < LINE_BYTES or 4 + metadata_size >= len(payload):
                    raise ValueError("callback metadata byte bound differs")
                metadata_raw = payload[4 : 4 + metadata_size]
                metadata = strict_json(metadata_raw)
                if type(metadata) is not dict or _canonical(metadata) != metadata_raw:
                    raise ValueError("callback metadata noncanonical JSON")
                if "data" in metadata:
                    raise ValueError("callback metadata carries duplicate data")
                receipt_data = self.drain.append_chunk(
                    {**metadata, "data": payload[4 + metadata_size :]}
                )
                receipt = _canonical(receipt_data)
                if len(receipt) > MAX_ACK:
                    raise ValueError("callback ACK byte bound differs")
            else:
                if self.drain is None:
                    raise ValueError("failure drain not bound")
                if not 0 < len(payload) <= MAX_SNAPSHOT:
                    raise ValueError("terminal snapshot byte bound differs")
                snapshot = _parse_failure_snapshot_v2(payload)
                if any(
                    getattr(snapshot, name) != getattr(self.native.command, name)
                    for name in IDENTITY
                ):
                    raise ValueError("terminal snapshot foreign command")
                # Keep exact producer bytes. Reconciliation and close are separate.
                self.terminal_bytes = bytes(payload)
                receipt = hashlib.sha256(payload).digest()
            self.sequence = sequence
            return encode_frame(ACK, sequence, self.native.key, receipt)
        except BaseException:
            self.poisoned = True
            raise
