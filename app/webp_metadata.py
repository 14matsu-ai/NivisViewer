"""Bounded WebP geometry hints without entering an image codec.

The RIFF/VP8/VP8L fields follow the WebP container specification. A hint does
not validate compressed pixels; the normal decoder still owns that validation.
Missing or incomplete EXIF means unknown geometry, not an unrotated guess.
"""
from __future__ import annotations

from PIL import Image


def probe_webp_size(data: bytes, *, file_size: int) -> tuple[int, int] | None:
    if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    riff_end = int.from_bytes(data[4:8], "little") + 8
    if riff_end > file_size or riff_end < 26 or riff_end % 2:
        return None
    kind = data[12:16]
    length = int.from_bytes(data[16:20], "little")
    if 20 + length + (length & 1) > riff_end:
        return None
    if kind == b"VP8 ":
        if len(data) < 30 or length < 10 or data[20] & 1 or data[23:26] != b"\x9d\x01\x2a":
            return None
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return (width, height) if width and height else None
    if kind == b"VP8L":
        if len(data) < 25 or length < 5 or data[20] != 0x2F:
            return None
        bits = int.from_bytes(data[21:25], "little")
        if bits >> 29:  # Only version zero is defined.
            return None
        return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    if kind != b"VP8X" or length != 10 or len(data) < 30:
        return None
    flags = data[20]
    if flags & 0xC1 or data[21:24] != b"\0\0\0":
        return None
    width = int.from_bytes(data[24:27], "little") + 1
    height = int.from_bytes(data[27:30], "little") + 1
    if width * height > 0xFFFFFFFF:
        return None
    if not flags & 0x08:  # No EXIF orientation to resolve, including animation.
        return width, height

    offset = 30
    while offset + 8 <= min(len(data), riff_end):
        chunk = data[offset:offset + 4]
        length = int.from_bytes(data[offset + 4:offset + 8], "little")
        start = offset + 8
        end = start + length
        following = end + (length & 1)
        if following > riff_end or end > len(data):
            return None
        if chunk == b"EXIF":
            payload = data[start:end]
            tiff = payload[6:] if payload.startswith(b"Exif\0\0") else payload
            if len(tiff) < 8 or tiff[:4] not in {b"II\x2a\0", b"MM\0\x2a"}:
                return None
            try:
                exif = Image.Exif()
                exif.load(payload)
                orientation = int(exif.get(274, 1))
            except Exception:
                return None
            if orientation not in range(1, 9):
                return None
            return (height, width) if orientation in {5, 6, 7, 8} else (width, height)
        offset = following
    return None
