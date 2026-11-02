"""Pure native PNG leaf checks; no browser or screenshot source is used."""

import struct
import zlib

import pytest


def png(width=1440, height=900, *, channels=3, filter_byte=0):
    def chunk(kind, body):
        payload = kind + body
        return struct.pack(">I", len(body)) + payload + struct.pack(">I", zlib.crc32(payload))

    header = struct.pack(">IIBBBBB", width, height, 8, 2 if channels == 3 else 6, 0, 0, 0)
    raw = (bytes([filter_byte]) + b"\0" * (width * channels)) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def test_full_and_crop_png_have_exact_lossless_geometry():
    from scripts.execution_capacity.native_png import validate_native_png

    assert validate_native_png(png(), width=1440, height=900) == 3
    assert validate_native_png(png(320, 64, channels=4), width=320, height=64) == 4


@pytest.mark.parametrize("defect", ["crc", "filter", "trailing", "wrong_geometry", "truncated"])
def test_native_png_rejects_corruption_and_incomplete_decode(defect):
    from scripts.execution_capacity.native_png import validate_native_png

    raw = png()
    if defect == "crc":
        raw = raw[:25] + bytes([raw[25] ^ 1]) + raw[26:]
    elif defect == "filter":
        raw = png(filter_byte=5)
    elif defect == "trailing":
        raw += b"after-iend"
    elif defect == "wrong_geometry":
        raw = png(1439, 900)
    else:
        raw = raw[:-4]
    with pytest.raises(ValueError, match="native PNG"):
        validate_native_png(raw, width=1440, height=900)
