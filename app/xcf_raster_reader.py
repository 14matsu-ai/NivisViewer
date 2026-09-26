"""Bounded, feature-gated XCF raster reader (no GIMP process or compositor).

Layout reference: https://developer.gimp.org/core/standards/xcf/
Unknown rendering features deliberately fall back to the authoritative renderer.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from io import BytesIO
import os
import struct
import zlib

from PIL import Image

from .gimp_xcf_backend import _check_cancelled


class UnsupportedXcf(ValueError):
    pass


class DeferXcf(BaseException):
    """Internal worker handoff; must cross image-source Exception wrappers."""


_DEFER = ContextVar("defer_xcf", default=False)


@contextmanager
def xcf_worker_scope(*, defer):
    token = _DEFER.set(defer)
    try:
        yield
    finally:
        _DEFER.reset(token)


def check_xcf_lane():
    if _DEFER.get():
        raise DeferXcf()


class Reader:
    def __init__(self, stream):
        self.f = stream
        self.f.seek(0, 2)
        self.length = self.f.tell()
        self.f.seek(0)
        self.wide = False

    def read(self, n):
        if n < 0 or n > 16 * 1024 * 1024:
            raise UnsupportedXcf("XCF field too large")
        data = self.f.read(n)
        if len(data) != n:
            raise UnsupportedXcf("Truncated XCF")
        return data

    def uint(self):
        return int.from_bytes(self.read(4), "big")

    def ptr(self):
        return int.from_bytes(self.read(8 if self.wide else 4), "big")

    def seek(self, offset):
        if not 0 < offset < self.length:
            raise UnsupportedXcf("Invalid XCF pointer")
        self.f.seek(offset)

    def props(self, allowed):
        result = {}
        for _ in range(256):
            kind, size = self.uint(), self.uint()
            if kind == 0:
                if size:
                    raise UnsupportedXcf("Invalid property terminator")
                return result
            if kind not in allowed or kind in result:
                raise UnsupportedXcf("Unsupported XCF property")
            result[kind] = self.read(size)
        raise UnsupportedXcf("Too many XCF properties")


def number(props, key, default, *, signed=False):
    if key not in props:
        return default
    if len(props[key]) != 4:
        raise UnsupportedXcf("Invalid scalar property")
    return int.from_bytes(props[key], "big", signed=signed)


def profile_from(props):
    if 21 not in props:
        return None
    reader = Reader(BytesIO(props[21]))
    profile = None
    while reader.f.tell() < reader.length:
        name = reader.read(reader.uint())
        reader.uint()  # persistence flags
        payload = reader.read(reader.uint())
        if name == b"icc-profile\0":
            profile = payload
        elif name not in (b"gimp-comment\0", b"gimp-image-metadata\0"):
            raise UnsupportedXcf("Unsupported image parasite")
    return profile


def tile_pixels(reader, offset, pixels, bpp, compression):
    reader.seek(offset)
    total = pixels * bpp
    if compression == 0:
        return reader.read(total)
    if compression == 2:
        # A compressed 64x64 tile has a small bounded worst-case size.
        packed = reader.f.read(total + 1024)
        dec = zlib.decompressobj()
        data = dec.decompress(packed, total + 1)
        if len(data) != total or not dec.eof:
            raise UnsupportedXcf("Invalid compressed tile")
        return data
    if compression != 1:
        raise UnsupportedXcf("Unsupported compression")
    output = bytearray(total)
    for channel in range(bpp):
        plane = bytearray()
        while len(plane) < pixels:
            code = reader.read(1)[0]
            repeat = code < 128
            count = (code + 1) if repeat else (256 - code)
            if code in (127, 128):
                count = int.from_bytes(reader.read(2), "big")
            if not 0 < count <= pixels - len(plane):
                raise UnsupportedXcf("Invalid RLE run")
            plane.extend(reader.read(1) * count if repeat else reader.read(count))
        output[channel::bpp] = plane
    return bytes(output)


def raster(reader, pointer, width, height, mode, compression):
    reader.seek(pointer)
    bpp = len(mode)
    if (reader.uint(), reader.uint(), reader.uint()) != (width, height, bpp):
        raise UnsupportedXcf("Invalid hierarchy")
    level = reader.ptr()
    reader.seek(level)
    if (reader.uint(), reader.uint()) != (width, height):
        raise UnsupportedXcf("Invalid level")
    positions = [(x, y) for y in range(0, height, 64) for x in range(0, width, 64)]
    pointers = [reader.ptr() for _ in positions]
    if reader.ptr() != 0:
        raise UnsupportedXcf("Invalid tile list")
    image = Image.new(mode, (width, height))
    try:
        for (x, y), tile in zip(positions, pointers):
            _check_cancelled()
            size = min(64, width - x), min(64, height - y)
            data = tile_pixels(reader, tile, size[0] * size[1], bpp, compression)
            with Image.frombytes(mode, size, data) as decoded:
                image.paste(decoded, (x, y))
        return image
    except BaseException:
        image.close()
        raise


def decode_xcf_raster(source, *, max_pixels=64 * 1024 * 1024):
    _check_cancelled()
    stream = (BytesIO(source) if isinstance(source, (bytes, bytearray, memoryview))
              else open(os.fspath(source), "rb"))
    with stream:
        r = Reader(stream)
        header = r.read(14)
        if header[:9] != b"gimp xcf " or header[-1:] != b"\0":
            raise UnsupportedXcf("Invalid XCF")
        if header[9:13] != b"file" and (header[9:10] != b"v" or not header[10:13].isdigit()):
            raise UnsupportedXcf("Invalid version tag")
        version = 0 if header[9:13] == b"file" else int(header[10:13])
        if not 0 <= version <= 26 or version in (4, 5, 6):
            raise UnsupportedXcf("Unsupported XCF version")
        r.wide = version >= 11
        width, height, color = r.uint(), r.uint(), r.uint()
        if not 0 < width * height <= max_pixels or not width or not height or color != 0:
            raise UnsupportedXcf("Unsupported canvas")
        if version >= 7 and r.uint() != 150:
            raise UnsupportedXcf("Only 8-bit nonlinear RGB is supported")
        # Editing-only properties plus compression and a bounded parasite list.
        props = r.props({17, 18, 19, 20, 21, 22, 25, 27, 39, 40})
        compression = props.get(17, b"\0")
        if len(compression) != 1:
            raise UnsupportedXcf("Invalid compression")
        profile = profile_from(props)
        pointers = []
        for _ in range(1025):
            pointer = r.ptr()
            if not pointer:
                break
            pointers.append(pointer)
        else:
            raise UnsupportedXcf("Too many layers")
        if not pointers or r.ptr():
            raise UnsupportedXcf("Empty image or channels requiring GIMP")
        layers = []
        for pointer in pointers:
            _check_cancelled()
            r.seek(pointer)
            lw, lh, kind = r.uint(), r.uint(), r.uint()
            r.read(r.uint())  # layer name, never used for display or logging
            p = r.props({2, 6, 7, 8, 9, 10, 11, 12, 13, 15, 20, 28, 32, 33,
                         34, 35, 36, 37, 39, 41, 42})
            hierarchy, mask = r.ptr(), r.ptr()
            if version >= 20 and r.ptr():
                raise UnsupportedXcf("Layer effects require GIMP")
            if not number(p, 8, 1):
                continue
            if kind not in (0, 1) or not 0 < lw * lh <= max_pixels or not lw or not lh:
                raise UnsupportedXcf("Unsupported layer pixels")
            if (mask and number(p, 11, 1)) or number(p, 13, 0):
                raise UnsupportedXcf("Active mask requires GIMP")
            mode = number(p, 7, 0)
            if mode not in (0, 28) or abs(number(p, 35, 1, signed=True)) != 1:
                raise UnsupportedXcf("Unsupported compositing")
            opacity = number(p, 6, 255)
            if opacity != 255 or (33 in p and p[33] != struct.pack(">f", 1.0)):
                raise UnsupportedXcf("Layer opacity requires GIMP")
            offsets = p.get(15, b"\0" * 8)
            if len(offsets) != 8:
                raise UnsupportedXcf("Invalid offsets")
            x, y = struct.unpack(">ii", offsets)
            layers.append((lw, lh, kind, hierarchy, x, y, mode, p))
        # A single layer needs no color-space blending. Multiple layers use
        # only explicitly nonlinear normal/union (legacy normal is nonlinear).
        if len(layers) > 1:
            for *_, mode, p in layers:
                if mode == 28 and abs(number(p, 36, 0, signed=True)) != 2:
                    raise UnsupportedXcf("Nonlinear composite space required")
                if mode == 0 and 36 in p and abs(number(p, 36, 2, signed=True)) != 2:
                    raise UnsupportedXcf("Unsupported legacy composite space")
        if len(layers) == 1:
            lw, lh, kind, hierarchy, x, y, _, _p = layers[0]
            if (lw, lh, x, y) == (width, height, 0, 0):
                # No canvas-sized duplicate or pointless alpha composite.
                result = raster(r, hierarchy, lw, lh, "RGB" if kind == 0 else "RGBA", compression[0])
                if profile:
                    result.info["icc_profile"] = profile
                return result
        result = Image.new("RGBA", (width, height))
        try:
            for lw, lh, kind, hierarchy, x, y, _, _p in reversed(layers):
                _check_cancelled()
                with raster(r, hierarchy, lw, lh, "RGB" if kind == 0 else "RGBA", compression[0]) as layer:
                    with layer.convert("RGBA") as rgba:
                        result.alpha_composite(rgba, (x, y))
            if profile:
                result.info["icc_profile"] = profile
            return result
        except BaseException:
            result.close()
            raise
