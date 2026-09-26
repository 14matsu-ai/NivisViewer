from threading import Event, Thread
import struct
import subprocess

from PIL import Image
import pytest
pytestmark = pytest.mark.usefixtures("enable_xcf")

from app import creative_image_decoder as decoder
from app import gimp_xcf_backend as backend


def masked_xcf(*, apply_mask=False, blend_mode=0):
    def u(*values):
        return struct.pack(">" + "I" * len(values), *values)
    header = b"gimp xcf file\0" + u(1, 1, 0) + u(0, 0)
    layer_pos = len(header) + 12
    layer = u(1, 1, 0, 2) + b"L\0"
    layer += u(6, 4, 255, 8, 4, 1, 7, 4, blend_mode, 11, 4, int(apply_mask), 0, 0)
    hierarchy = layer_pos + len(layer) + 8
    mask_pos = hierarchy + 20 + 16 + 3
    mask = u(1, 1, 2) + b"M\0" + u(0, 0)
    mask_hierarchy = mask_pos + len(mask) + 4
    return (header + u(layer_pos, 0, 0) + layer + u(hierarchy, mask_pos)
            + u(1, 1, 3, hierarchy + 20, 0)
            + u(1, 1, hierarchy + 36, 0) + bytes((220, 30, 40))
            + mask + u(mask_hierarchy)
            + u(1, 1, 1, mask_hierarchy + 20, 0)
            + u(1, 1, mask_hierarchy + 36, 0) + b"\0")


def test_disabled_mask_actual_xcf_retains_visible_rgb(monkeypatch):
    from gimpformats.gimpXcfDocument import GimpDocument
    payload = masked_xcf()
    document = GimpDocument()
    document.decode(payload)
    assert document.raw_layers[0].applyMask is False
    assert document.raw_layers[0].mask is not None
    with document.image as wrong:
        assert wrong.getpixel((0, 0)) == (0, 0, 0, 0)
    monkeypatch.setattr(decoder, "render_xcf_with_gimp",
                        lambda _: pytest.fail("Disabled mask needs no external renderer"))
    with decoder.decode_creative_image(payload, suffix=".xcf") as image:
        assert image.convert("RGBA").getpixel((0, 0)) == (220, 30, 40, 255)


@pytest.mark.parametrize("settings", [{"apply_mask": True}, {"blend_mode": 1}])
def test_compositing_features_use_gimp_instead_of_silent_approximation(monkeypatch, settings):
    calls = []
    def render(source):
        calls.append(source)
        return Image.new("RGBA", (1, 1), (10, 20, 30, 40))
    monkeypatch.setattr(decoder, "render_xcf_with_gimp", render)
    payload = masked_xcf(**settings)
    with decoder.decode_creative_image(payload, suffix=".xcf") as image:
        assert image.getpixel((0, 0)) == (10, 20, 30, 40)
    assert calls == [payload]


class FakeProcess:
    def __init__(self):
        self.started = Event()
        self.returncode = None
        self.terminated = False
        self.reaped = False

    def wait(self, timeout=None):
        self.started.set()
        if self.returncode is None:
            Event().wait(min(timeout or 0.01, 0.01))
            raise subprocess.TimeoutExpired("fake-gimp", timeout)
        self.reaped = True
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.returncode = -9


def test_cancelled_lock_wait_never_starts_gimp(monkeypatch):
    monkeypatch.setattr(backend, "find_gimp3_executable", lambda: "fake-gimp")
    monkeypatch.setattr(backend, "_run_gimp", lambda *a: pytest.fail("cancelled wait started GIMP"))
    cancelled = Event()
    ready = Event()
    errors = []
    def worker():
        with backend.gimp_cancellation(cancelled):
            ready.set()
            try:
                backend.render_xcf_with_gimp(b"test")
            except backend.GimpXcfCancelled as exc:
                errors.append(exc)
    with backend._GIMP_RENDER_LOCK:
        thread = Thread(target=worker)
        thread.start()
        assert ready.wait(1)
        cancelled.set()
        thread.join(1)
        assert not thread.is_alive()
        assert errors


def test_precancelled_request_does_not_even_discover_gimp(monkeypatch):
    monkeypatch.setattr(backend, "find_gimp3_executable",
                        lambda: pytest.fail("Cancelled request performed discovery"))
    cancelled = Event()
    cancelled.set()
    with backend.gimp_cancellation(cancelled), pytest.raises(backend.GimpXcfCancelled):
        backend.render_xcf_with_gimp(b"test")
    assert backend._CANCEL_TOKEN.get() is None


def test_timeout_reaps_owned_process(monkeypatch):
    process = FakeProcess()
    monkeypatch.setattr(backend.subprocess, "Popen", lambda *a, **k: process)
    with pytest.raises(subprocess.TimeoutExpired):
        backend._run_process(["fake-gimp"], timeout=0.01)
    assert process.terminated and process.reaped


def test_unsupported_composite_without_gimp_is_error_not_wrong_pixels(monkeypatch):
    def unavailable(_source):
        raise backend.GimpXcfBackendUnavailable("missing")
    monkeypatch.setattr(decoder, "render_xcf_with_gimp", unavailable)
    with pytest.raises(decoder.CreativeImageError):
        decoder.decode_creative_image(masked_xcf(apply_mask=True), suffix=".xcf")


def test_browser_cancel_reaches_owned_process_and_reaps_it(monkeypatch, tmp_path):
    from app.thumbnail_provider import BrowserThumbnailProvider, _invoke_thumbnail_loader
    from app.browser_model import BrowserItem, BrowserItemKind
    from test_creative_image_decoder import _xcf_header
    process = FakeProcess()
    monkeypatch.setattr(backend.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(backend, "find_gimp3_executable", lambda: "fake-gimp")
    path = tmp_path / "modern.xcf"
    path.write_bytes(_xcf_header(26, size=(1, 1)))
    item = BrowserItem(path.name, path, BrowserItemKind.IMAGE, path.stat().st_mtime)
    cancelled = Event()
    results = []
    thread = Thread(target=lambda: results.append(_invoke_thumbnail_loader(
        BrowserThumbnailProvider.load_thumbnail, item, 80, cancelled)))
    thread.start()
    try:
        assert process.started.wait(1)
        cancelled.set()
        thread.join(2)
        assert not thread.is_alive()
        assert process.terminated and process.reaped
        assert results == [None]
        assert backend._GIMP_RENDER_LOCK.acquire(blocking=False)
        backend._GIMP_RENDER_LOCK.release()
    finally:
        cancelled.set()
        thread.join(2)


@pytest.mark.parametrize("wired", [False, True])
def test_viewer_cancelled_xcf_releases_single_worker_for_png(monkeypatch, tmp_path, qapp, wired):
    from contextlib import nullcontext
    from PySide6.QtTest import QTest
    from app import zip_raster_book_runtime as runtime_module
    from app.folder_raster_book_runtime import FolderRasterBookRuntime
    from app.image_source import FolderImageSource
    from test_folder_raster_book_runtime import _unit, _request, _wait_until
    from test_creative_image_decoder import _xcf_header
    (tmp_path / "0.xcf").write_bytes(_xcf_header(26, size=(1, 1)))
    Image.new("RGB", (1, 1), "red").save(tmp_path / "1.png")
    process = FakeProcess()
    if not wired:
        # Control reproduces the former missing cancellation connection.
        monkeypatch.setattr(runtime_module, "gimp_cancellation", lambda token: nullcontext())
    monkeypatch.setattr(backend.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(backend, "find_gimp3_executable", lambda: "fake-gimp")
    source = FolderImageSource(tmp_path)
    runtime = FolderRasterBookRuntime(source, 1, max_active_jobs=1)
    frames = []
    runtime.frameReady.connect(frames.append)
    try:
        assert runtime.request(_request(1, _unit(source, 0)))
        assert process.started.wait(1)
        assert runtime.request(_request(2, _unit(source, 1)))
        if not wired:
            QTest.qWait(150)
            qapp.processEvents()
            assert frames == []
            assert not process.terminated
            process.returncode = 1  # Finish only the fake conversion manually.
        _wait_until(qapp, lambda: bool(frames), timeout_ms=2000)
        assert [frame.request_id for frame in frames] == [2]
        assert process.terminated is wired
        assert process.reaped
    finally:
        process.returncode = 1
        runtime.cancel()
        assert runtime.shutdown(wait_msecs=3000)
