import struct
import zlib
from threading import Event
from time import monotonic
import zipfile

import pytest
pytestmark = pytest.mark.usefixtures("enable_xcf")
from PIL import Image
from PySide6.QtTest import QTest

from app import creative_image_decoder as creative
from app.xcf_raster_reader import decode_xcf_raster, UnsupportedXcf


def xcf_bytes(images, *, version=23, compression=2, extra=b"", composite=2):
    """Synthetic raster-only XCF; images are in top-to-bottom layer order."""
    u = lambda *v: struct.pack(">" + "I" * len(v), *v)
    psize = 8 if version >= 11 else 4
    ptr = lambda v: v.to_bytes(psize, "big")
    prop = lambda k, b: u(k, len(b)) + b
    data = bytearray(b"gimp xcf " + (b"file" if not version else f"v{version:03}".encode()) + b"\0")
    data += u(*images[0].size, 0)
    if version >= 4:
        data += u(150)
    data += prop(17, bytes([compression])) + u(0, 0)
    table = len(data)
    data += b"\0" * psize * (len(images) + 2)
    for i, image in enumerate(images):
        data[table+i*psize:table+(i+1)*psize] = ptr(len(data))
        data += u(*image.size, 1 if image.mode == "RGBA" else 0, 2) + b"L\0"
        data += prop(7, u(28 if version >= 10 else 0)) + prop(8, u(1))
        data += prop(36, u(composite)) + extra + u(0, 0)
        hptr = len(data)
        data += b"\0" * psize * (3 if version >= 20 else 2)
        data[hptr:hptr+psize] = ptr(len(data))
        data += u(*image.size, len(image.mode))
        data += ptr(len(data)+2*psize) + ptr(0)
        data += u(*image.size)
        tiles = [(x,y) for y in range(0,image.height,64) for x in range(0,image.width,64)]
        tptr = len(data)
        data += b"\0" * psize * (len(tiles)+1)
        for j,(x,y) in enumerate(tiles):
            data[tptr+j*psize:tptr+(j+1)*psize] = ptr(len(data))
            with image.crop((x,y,min(x+64,image.width),min(y+64,image.height))) as tile:
                raw = tile.tobytes()
            if compression == 2:
                data += zlib.compress(raw)
            elif compression == 1:
                for c in range(len(image.mode)):
                    plane = raw[c::len(image.mode)]
                    data += b"\x80" + len(plane).to_bytes(2,"big") + plane
            else:
                data += raw
    return bytes(data)


@pytest.mark.parametrize("version,compression", [(0,0),(3,1),(10,1),(11,2),(19,2),(23,2),(26,2)])
def test_direct_tiled_modern_and_legacy_pixels(tmp_path, monkeypatch, version, compression):
    monkeypatch.setattr(creative, "render_xcf_with_gimp", lambda _: pytest.fail("GIMP launched"))
    source = Image.frombytes("RGB", (73,69), bytes((i*37)%256 for i in range(73*69*3)))
    data = xcf_bytes([source], version=version, compression=compression)
    path = tmp_path / "日本語.xcf"
    path.write_bytes(data)
    for payload in (path, data):
        with creative.decode_creative_image(payload, suffix=".xcf") as decoded:
            assert decoded.convert("RGB").tobytes() == source.tobytes()


def test_normal_layers_and_unsupported_feature_gate():
    bottom = Image.new("RGBA", (3,2), (10,20,30,255))
    top = Image.new("RGBA", (3,2), (210,100,50,128))
    with decode_xcf_raster(xcf_bytes([top,bottom])) as decoded:
        assert decoded.tobytes() == Image.alpha_composite(bottom,top).tobytes()
    # Modern linear blending must not be approximated using Pillow's gamma blend.
    with pytest.raises(UnsupportedXcf):
        decode_xcf_raster(xcf_bytes([top,bottom],composite=1))
    unknown = struct.pack(">II", 47, 0)  # vector layer
    with pytest.raises(UnsupportedXcf):
        decode_xcf_raster(xcf_bytes([bottom],extra=unknown))


def test_direct_decode_cancellation_between_tiles(monkeypatch):
    from app import xcf_raster_reader as reader
    from app import gimp_xcf_backend as backend
    image = Image.new("RGB", (256,256))
    payload = xcf_bytes([image])
    cancelled = Event()
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls == 5:
            cancelled.set()
        backend._check_cancelled()
    monkeypatch.setattr(reader, "_check_cancelled", check)
    with backend.gimp_cancellation(cancelled), pytest.raises(backend.GimpXcfCancelled):
        reader.decode_xcf_raster(payload)
    assert calls == 5


@pytest.mark.parametrize("archive", [False, True])
@pytest.mark.parametrize("coordinated", [False, True])
def test_slow_xcf_does_not_block_png_thumbnail(qapp, tmp_path, monkeypatch, archive, coordinated):
    from app.browser_model import BrowserItem, BrowserItemKind
    from app.thumbnail_provider import BrowserThumbnailProvider
    from app.image_work_coordinator import ImageWorkCoordinator
    from test_creative_image_decoder import _xcf_header
    entered, release = Event(), Event()
    def slow(_):
        entered.set()
        assert release.wait(5)
        return Image.new("RGB", (1,1), "red")
    monkeypatch.setattr(creative,"render_xcf_with_gimp",slow)
    xcf = tmp_path / ("遅い.zip" if archive else "遅い.xcf")
    if archive:
        with zipfile.ZipFile(xcf,"w") as z:
            z.writestr("image.xcf",_xcf_header(26,size=(1,1)))
    else:
        xcf.write_bytes(_xcf_header(26,size=(1,1)))
    png = tmp_path / "普通.png"
    Image.new("RGB",(8,8),"blue").save(png)
    coordinator = ImageWorkCoordinator() if coordinated else None
    provider = BrowserThumbnailProvider(disk_cache_enabled=False, image_work_coordinator=coordinator)
    ready = []
    provider.thumbnail_ready.connect(lambda path,*_:ready.append(path))
    def pump(predicate):
        deadline = monotonic()+3
        while not predicate() and monotonic()<deadline:
            qapp.processEvents()
            QTest.qWait(5)
        assert predicate()
    try:
        generation = provider.begin_generation()
        kind = BrowserItemKind.ARCHIVE if archive else BrowserItemKind.IMAGE
        provider.request(BrowserItem(xcf.name,xcf,kind,xcf.stat().st_mtime),64,generation=generation)
        pump(entered.is_set)
        provider.request(BrowserItem(png.name,png,BrowserItemKind.IMAGE,png.stat().st_mtime),64,generation=generation)
        pump(lambda: str(png) in ready)
        assert str(xcf) not in ready
        provider.begin_generation()  # stale XCF must never be published
        release.set()
        pump(lambda:provider.wait_for_done(0))
        qapp.processEvents()
        assert str(xcf) not in ready
    finally:
        release.set()
        provider.close(wait_msecs=3000)
        if coordinator:
            assert coordinator.shutdown(wait_msecs=3000)
