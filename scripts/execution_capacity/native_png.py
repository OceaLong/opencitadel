"""Bounded Python leaf for the existing native PNG wire and retained crop.

The native collector validates the same PNG structure before emitting capture
records. This independent retained-byte check never infers a missing original
full screenshot from a crop or a SHA digest.
"""

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
FULL_BYTES = 8 * 1024 * 1024
CROP_BYTES = 96 * 1024


def validate_native_png(value, *, width, height):
    if (
        type(value) is not bytes
        or type(width) is not int
        or type(height) is not int
        or not 1 <= width <= 1440
        or not 1 <= height <= 900
        or len(value) <= 32
        or len(value) > (FULL_BYTES if (width, height) == (1440, 900) else CROP_BYTES)
        or not value.startswith(SIGNATURE)
    ):
        raise ValueError("native PNG size/signature/geometry differs")
    position = len(SIGNATURE)
    header = ended = False
    channels = 0
    compressed = []
    while position < len(value):
        if position + 12 > len(value):
            raise ValueError("native PNG chunk truncated")
        length = struct.unpack_from(">I", value, position)[0]
        end = position + 12 + length
        if length > FULL_BYTES or end > len(value):
            raise ValueError("native PNG chunk length differs")
        kind = value[position + 4 : position + 8]
        body = memoryview(value)[position + 8 : position + 8 + length]
        recorded_crc = struct.unpack_from(">I", value, position + 8 + length)[0]
        if zlib.crc32(value[position + 4 : position + 8 + length]) != recorded_crc:
            raise ValueError("native PNG chunk CRC differs")
        if not header:
            if kind != b"IHDR" or length != 13:
                raise ValueError("native PNG header absent")
            actual_width, actual_height, depth, color, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", body)
            )
            if (
                (actual_width, actual_height) != (width, height)
                or depth != 8
                or color not in (2, 6)
                or compression != 0
                or filtering != 0
                or interlace != 0
            ):
                raise ValueError("native PNG geometry/format differs")
            channels = 3 if color == 2 else 4
            header = True
        elif kind == b"IDAT":
            compressed.append(body)
        elif kind == b"IEND":
            if length or end != len(value):
                raise ValueError("native PNG trailing bytes")
            ended = True
        elif not kind or not kind[0] & 32:
            raise ValueError("native PNG unknown critical chunk")
        position = end
    if not ended or not compressed:
        raise ValueError("native PNG image stream incomplete")
    stride = width * channels + 1
    expected = stride * height
    inflater = zlib.decompressobj()
    raw = bytearray()
    for block in compressed:
        raw.extend(inflater.decompress(block, expected - len(raw) + 1))
        if len(raw) > expected or inflater.unconsumed_tail or inflater.unused_data:
            raise ValueError("native PNG inflate bound differs")
    raw.extend(inflater.flush(expected - len(raw) + 1))
    if len(raw) != expected or not inflater.eof or inflater.unconsumed_tail or inflater.unused_data:
        raise ValueError("native PNG decoded size/EOF differs")
    if any(raw[row * stride] > 4 for row in range(height)):
        raise ValueError("native PNG row filter differs")
    return channels
