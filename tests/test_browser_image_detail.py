from threading import get_ident

import pytest
from PIL import Image
from pillow_jxl.JpegXLImagePlugin import JXLImageFile

from app.browser_image_detail import BrowserImageDetailProbe, BrowserImageDetailResult


@pytest.mark.parametrize("corrupt", [True, False])
def test_jxl_probe_completes_and_releases_worker(tmp_path, qapp, monkeypatch, corrupt):
    path = tmp_path / "画像情報.jxl"
    if corrupt:
        path.write_bytes(b"\xff\x0a")
    else:
        with Image.new("RGB", (48, 64), "red") as image:
            image.save(path, format="JXL", lossless=True)

    gui_thread = get_ident()
    decode_threads = []
    original_open = JXLImageFile._open

    def record_open(image):
        decode_threads.append(get_ident())
        return original_open(image)

    monkeypatch.setattr(JXLImageFile, "_open", record_open)
    probe = BrowserImageDetailProbe()
    results = []
    probe.completed.connect(results.append)
    try:
        probe.request(path, generation=73)
        assert probe._pool.waitForDone(3000)
        qapp.processEvents()
        assert results == [BrowserImageDetailResult(
            str(path), 73, None if corrupt else (48, 64),
        )]
        assert not probe._workers
        assert decode_threads and all(thread != gui_thread for thread in decode_threads)
    finally:
        assert probe.close(3000)
        qapp.processEvents()
