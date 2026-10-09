"""Strict retained native source envelope, not an observed byte count or pass.

Every image/trace byte remains in its exact host-ACKed NDJSON chunk. The
failure-drain callback, C2c originals, scratch index, and fresh copy have
independent envelopes and MUST be added before any total cap is accepted.
"""

import json

from scripts.execution_capacity.native_raw import LINE_BYTES

CHUNK = 48 * 1024
FULL_IMAGE = 8 * 1024 * 1024
PROGRESS_CROP = 96 * 1024
TRACE = 32 * 1024 * 1024
LOADED_WINDOWS = 1100
CONTEXTS_PER_WINDOW = 10
FULL_IMAGES_PER_WINDOW = 11
CROPS_PER_WINDOW = 1200
TRACE_STREAMS_PER_WINDOW = 1
KNOWN_PUBLIC_ROLE_BYTES_WITH_MARGIN = 25_979_778_600
FAILURE_FULL_IMAGES_PER_ROUND = 11
PHYSICAL_ROUNDS = 1200


def _line(kind, size, chunk_index):
    """Safe maximum JS JSON.stringify byte spelling for this closed record.

    Each accepted ID can contain 255 NUL controls; JSON.stringify escapes
    each as six ASCII bytes. Unpaired surrogates are rejected by the shared
    Python model and must be rejected by C3b's TS producer before emission.
    A decoded chunk is canonical base64.
    This is an encoded upper envelope, not typical PNG compressibility.
    """
    if kind not in ("image", "native-trace") or not 1 <= size <= CHUNK:
        raise ValueError("closed native sizing record required")
    maximal_id = "\u0000" * 255
    data = "A" * (4 * ((size + 2) // 3))
    record = {
        "wire_version": 1,
        "attempt_id": maximal_id,
        "protocol_id": maximal_id,
        "sample_id": maximal_id,
        "action_id": maximal_id,
        "context_id": maximal_id,
        "page_id": maximal_id,
        "window_id": maximal_id,
        "clock_id": maximal_id,
        "sequence": 65536,
        "received_ns": "9" * 20,
        "observation": (
            {"kind": "image", "capture_id": maximal_id, "chunk_index": chunk_index, "data": data}
            if kind == "image"
            else {
                "kind": "private-chunk",
                "artifact_id": maximal_id,
                "purpose": "native-trace",
                "chunk_index": chunk_index,
                "data": data,
            }
        ),
    }
    encoded = json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode()
    if len(encoded) + 1 >= LINE_BYTES:
        raise ValueError("legal maximal native record cannot fit frozen wire line")
    return len(encoded) + 1


def _artifact(kind, size):
    if not 0 < size <= (TRACE if kind == "native-trace" else FULL_IMAGE):
        raise ValueError("closed native artifact size required")
    whole, tail = divmod(size, CHUNK)
    wire = sum(_line(kind, CHUNK, index) for index in range(whole))
    if tail:
        wire += _line(kind, tail, whole)
    return wire


def success_native_wire_upper():
    return LOADED_WINDOWS * (
        FULL_IMAGES_PER_WINDOW * _artifact("image", FULL_IMAGE)
        + CROPS_PER_WINDOW * _artifact("image", PROGRESS_CROP)
        + TRACE_STREAMS_PER_WINDOW * _artifact("native-trace", TRACE)
    )


def failure_acquired_png_upper():
    """Only acquired retained PNG bytes; separate callback wire is unresolved."""
    return PHYSICAL_ROUNDS * FAILURE_FULL_IMAGES_PER_ROUND * FULL_IMAGE
