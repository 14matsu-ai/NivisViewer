from contextlib import contextmanager
from io import BytesIO
from threading import Event, Thread, get_ident

import pytest
from PIL import Image, ImageOps
from PySide6.QtCore import QRectF

from app.archive_backend import ArchiveBackendError, ArchiveEntry, ArchiveErrorCode, ArchiveListing
from app.archive_backend_registry import ArchiveBackendRegistry
from app.config_manager import ConfigManager
from app.image_source import SevenZipImageSource, ImageSourceError
from app.raster_book_runtime import RasterBookRuntime
from app.viewer_navigation_policy import NavigationInputKind
from app.viewer_window import ViewerWindow
from tests.test_zip_raster_viewer_integration import _wait_until


def jpeg(orientation=1):
    with Image.new("RGB", (1600, 2400), "red") as image:
        image.paste("blue", (800, 0, 1600, 1200))
        image.paste("green", (0, 1200, 800, 2400))
        image.paste("yellow", (800, 1200, 1600, 2400))
        exif = Image.Exif()
        exif[274] = orientation
        output = BytesIO()
        image.save(output, "JPEG", quality=95, exif=exif)
        return output.getvalue()


class Backend:
    def __init__(self, payload=None, pages=6):
        self.payload = payload or jpeg()
        self.pages = pages
        self.reads = []
        self.threads = []
        self.listings = 0
        self.block = set()
        self.started = Event()
        self.release = Event()
        self.cancelled = Event()

    def is_available(self):
        return True

    def list_entries(self, path, **kwargs):
        self.listings += 1
        return ArchiveListing(str(path), tuple(
            ArchiveEntry(f"{i}.jpg", len(self.payload), None, False, False)
            for i in range(self.pages)), "rar", None, False)

    def read_entry(self, path, entry, *, cancel_token=None, maximum_bytes=None):
        self.reads.append(entry)
        self.threads.append(get_ident())
        if entry in self.block:
            self.started.set()
            while not self.release.wait(0.005):
                if cancel_token.is_set():
                    self.cancelled.set()
                    raise ArchiveBackendError(ArchiveErrorCode.PROCESS_CANCELLED)
        if cancel_token.is_set():
            raise ArchiveBackendError(ArchiveErrorCode.PROCESS_CANCELLED)
        return self.payload


def source_fixture(tmp_path, backend, name="book.rar"):
    path = tmp_path / name
    path.write_bytes(b"fake archive identity")
    source = SevenZipImageSource(path, backend=backend)
    source.set_payload_cache_budget(8 * 1024 * 1024)
    return source


@pytest.mark.parametrize("orientation", range(1, 9))
def test_target_tier_orientation_pixels_and_full_upgrade_reuse(tmp_path, orientation):
    backend = Backend(jpeg(orientation))
    source = source_fixture(tmp_path, backend)
    try:
        preview = source.open_compatible_jpeg_at_most("0.jpg", (300, 400))
        full = source.open_compatible_jpeg_at_most("0.jpg", (None, None))
        with Image.open(BytesIO(backend.payload)) as raw:
            expected = ImageOps.exif_transpose(raw)
            assert preview.original_size == full.original_size == expected.size
            assert (full.qimage.width(), full.qimage.height()) == expected.size
            assert preview.qimage.sizeInBytes() < full.qimage.sizeInBytes() / 4
            # Check oriented quadrant colors, not just the decoder's own size calculation.
            for fx, fy in ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)):
                color = expected.getpixel((int(expected.width * fx), int(expected.height * fy)))
                actual = preview.qimage.pixelColor(int(preview.qimage.width() * fx), int(preview.qimage.height() * fy))
                assert max(abs(a - b) for a, b in zip(color, actual.getRgb()[:3])) < 8
            expected.close()
        assert backend.reads == ["0.jpg"]
        assert source.payload_cache_hits == 1
        assert source.payload_cache_bytes == len(backend.payload)
    finally:
        source.close()
    assert source.payload_cache_bytes == 0


def test_payload_lru_budget_eviction_oversize_and_close(tmp_path):
    backend = Backend()
    source = source_fixture(tmp_path, backend)
    size = len(backend.payload)
    source.set_payload_cache_budget(size * 2)
    for page in ("0.jpg", "1.jpg", "0.jpg", "2.jpg"):
        source._read_payload(page)
        assert source.payload_cache_bytes <= size * 2
    assert backend.reads == ["0.jpg", "1.jpg", "2.jpg"]
    source._read_payload("1.jpg")
    assert backend.reads[-1] == "1.jpg"  # LRU, not the touched page 0.
    source.set_payload_cache_budget(size - 1)
    assert source.payload_cache_bytes == 0
    source._read_payload("0.jpg")
    assert source.payload_cache_bytes == 0
    source.close()
    with pytest.raises(ImageSourceError):
        source._read_payload("0.jpg")


@pytest.mark.parametrize("change", ["replace", "close", "cancel"])
def test_inflight_payload_never_retained_after_invalidation(tmp_path, change):
    backend = Backend()
    source = source_fixture(tmp_path, backend)
    entered, release = Event(), Event()
    errors = []
    def ignores_cancel(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return backend.payload
    backend.read_entry = ignores_cancel
    def read():
        try:
            source._read_payload("0.jpg")
        except ImageSourceError as exc:
            errors.append(exc.code)
    thread = Thread(target=read)
    thread.start()
    try:
        assert entered.wait(2)
        if change == "replace":
            source.source_path.write_bytes(b"replacement is different")
        elif change == "close":
            source.close()
        else:
            source.cancel_image_request("0.jpg")
    finally:
        release.set()
        thread.join(3)
        source.close()
    assert errors
    assert source.payload_cache_bytes == 0


@pytest.mark.parametrize("failure", [ArchiveErrorCode.CORRUPT_ARCHIVE, ArchiveErrorCode.PASSWORD_REQUIRED,
                                    ArchiveErrorCode.PROCESS_TIMEOUT, ArchiveErrorCode.ENTRY_NOT_FOUND])
def test_backend_errors_not_cached_or_reclassified(tmp_path, failure):
    backend = Backend()
    source = source_fixture(tmp_path, backend)
    def fail(*args, **kwargs):
        raise ArchiveBackendError(failure)
    backend.read_entry = fail
    with pytest.raises(ImageSourceError) as error:
        source.open_image("0.jpg")
    assert error.value.code == failure.value
    assert source.payload_cache_bytes == 0
    source.close()


@pytest.mark.parametrize("payload", [b"", b"bad jpeg"])
def test_incomplete_or_corrupt_image_cannot_remain_cached(tmp_path, payload):
    backend = Backend()
    source = source_fixture(tmp_path, backend)
    backend.read_entry = lambda *args, **kwargs: payload
    with pytest.raises(ImageSourceError):
        source.open_compatible_jpeg_at_most("0.jpg", (100, 100))
    assert source.payload_cache_bytes == 0
    source.close()


def test_multivolume_keeps_backend_but_does_not_cache_first_volume_identity(tmp_path):
    backend = Backend()
    source = source_fixture(tmp_path, backend, "book.part01.rar")
    assert source.payload_cache_budget == 0
    for _ in range(2):
        source._read_payload("0.jpg")
    assert backend.reads == ["0.jpg", "0.jpg"]
    source.close()


@contextmanager
def viewer_fixture(tmp_path, qapp, backend):
    archive = tmp_path / "book.rar"
    archive.write_bytes(b"fake archive")
    config = ConfigManager(tmp_path / "settings.json")
    config.load()
    config.apply({"view_mode": "single", "viewer_memory_mode": "256"})
    registry = ArchiveBackendRegistry(winrar_backend=backend)
    window = ViewerWindow(config_manager=config, archive_backend_registry=registry)
    window.resize(640, 480)
    window.show()
    qapp.processEvents()
    try:
        assert window.open_path(archive)
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 0)
        yield window
    finally:
        backend.release.set()
        window.prepare_shutdown()
        window.close()
        qapp.processEvents()
        registry.close()


def test_discrete_admission_shared_runtime_budget_and_archive_replacement(tmp_path, qapp):
    backend = Backend()
    backend.block = {"1.jpg", "3.jpg"}
    with viewer_fixture(tmp_path, qapp, backend) as window:
        runtime = window.book_session.viewer_runtime
        source = window.book_session.source
        assert type(runtime) is RasterBookRuntime
        assert source.payload_cache_budget == 32 * 1024 * 1024
        assert runtime.cache_byte_budget + source.payload_cache_budget == 256 * 1024 * 1024
        window._go_to_index_with_history(3)
        assert runtime._current_request.current.pages[0].page_index == 3
        assert not window._decode_demand_timer.isActive()
        assert not runtime._dispatch_suspended
        _wait_until(qapp, lambda: "3.jpg" in backend.reads)
        assert all(thread != get_ident() for thread in backend.threads)
        # Replacement after extraction started must not publish that old frame.
        source.source_path.write_bytes(b"a different archive")
        backend.release.set()
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert window.presentation_state.displayed_page != 3
        assert source.payload_cache_bytes == 0


def test_rapid_wheel_retains_started_read_and_holds_unready_target(tmp_path, qapp):
    backend = Backend()
    backend.block = {"1.jpg", "4.jpg"}
    with viewer_fixture(tmp_path, qapp, backend) as window:
        runtime = window.book_session.viewer_runtime
        _wait_until(qapp, lambda: "1.jpg" in backend.reads)
        started_job = runtime._active_job
        commits = []
        window.presentationCommitted.connect(lambda c: commits.append(c.frame.unit.focused_index))
        window.next_page(input_kind=NavigationInputKind.WHEEL)  # Adopt started page 1.
        assert runtime._active_job is started_job
        assert not started_job.cancelled.is_set()
        assert backend.reads.count("1.jpg") == 1
        for _ in range(3):
            window.next_page(input_kind=NavigationInputKind.WHEEL)
        # The foreground runtime keeps one worker slot. Further same-direction
        # notches are consumed until the first cold page is complete.
        assert not runtime._dispatch_suspended
        assert runtime._current_request.current.pages[0].page_index == 1
        window._finish_wheel_navigation()
        assert "4.jpg" not in backend.reads
        assert "2.jpg" not in backend.reads and "3.jpg" not in backend.reads
        assert commits == []
        assert not backend.cancelled.is_set()
        backend.release.set()
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 1)
        assert "4.jpg" not in backend.reads
        assert 1 in runtime.cached_page_indexes
        assert commits == [1]


@pytest.mark.parametrize("direction", ["ltr", "rtl"])
def test_spread_direction_zoom_tiers_and_cached_revisit(tmp_path, qapp, direction):
    backend = Backend()
    with viewer_fixture(tmp_path, qapp, backend) as window:
        window.set_reading_direction(direction)
        window.set_single_first_page(False)
        window.set_view_mode("spread")
        runtime = window.book_session.viewer_runtime
        _wait_until(qapp, lambda: len(window.viewer.displayed_page_indexes) == 2)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        reads = len(backend.reads)
        # A full-source upgrade uses the retained compressed bytes, not a new process.
        source = window.book_session.source
        full = source.open_compatible_jpeg_at_most("0.jpg", (None, None))
        assert full.original_size == (1600, 2400)
        assert full.qimage.height() == 2400
        window.next_page()
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 2)
        window.previous_page()
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 0)
        assert len(backend.reads) == reads
        assert source.payload_cache_bytes <= source.payload_cache_budget


def test_real_viewer_loupe_upgrades_preview_without_reextraction(tmp_path, qapp):
    backend = Backend(pages=1)
    with viewer_fixture(tmp_path, qapp, backend) as window:
        runtime = window.book_session.viewer_runtime
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        source_image = window.viewer._images[0]
        assert source_image.source_is_preview
        assert backend.reads == ["0.jpg"]
        window.viewer.magnifier_selecting = True
        window.viewer.magnifier_source_page = 0
        window.viewer._magnifier_source_image_id = source_image.image_id
        window.viewer.magnifier_source_rect = QRectF(0, 0, 200, 300)
        window.viewer._request_magnifier_render()
        _wait_until(qapp, lambda: window.viewer.magnifier_active)
        _wait_until(qapp, lambda: not window.viewer._images[0].source_is_preview)
        assert window.viewer._magnifier_pixmap is not None
        assert window.viewer._images[0].qimage.height() == 2400
        assert backend.reads == ["0.jpg"]
        assert all(thread != get_ident() for thread in backend.threads)


def test_book_switch_cancels_old_work_and_discards_payloads(tmp_path, qapp):
    backend = Backend()
    backend.block = {"1.jpg"}
    with viewer_fixture(tmp_path, qapp, backend) as window:
        old_source = window.book_session.source
        old_generation = window.book_session.generation
        _wait_until(qapp, lambda: backend.started.is_set())
        replacement = tmp_path / "second.rar"
        replacement.write_bytes(b"different archive identity")
        assert window.open_path(replacement)
        _wait_until(qapp, lambda: window.book_session.generation != old_generation
                    and window.book_session.current_path == replacement
                    and window.presentation_state.displayed_page == 0)
        _wait_until(qapp, lambda: old_source._closed.is_set())
        backend.release.set()
        assert old_source.payload_cache_bytes == 0
        assert window.book_session.source is not old_source
        assert window.presentation_state.displayed_page == 0


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
def test_dpi_rotation_and_manual_zoom_keep_source_tiers(tmp_path, qapp, monkeypatch, dpr):
    backend = Backend(pages=1)
    with viewer_fixture(tmp_path, qapp, backend) as window:
        # Replace only DPR observation, never the renderer or navigation path.
        with monkeypatch.context() as patch:
            patch.setattr(window.viewer, "devicePixelRatioF", lambda: dpr)
            window.rotate_right()
            _wait_until(qapp, lambda: window.book_session.viewer_runtime._current_request.render_spec.rotation == 90
                        and not window.book_session.viewer_runtime.has_unfinished_tasks())
            request = window._zip_runtime_request(window.model.spread_at())
            assert request.render_spec.device_pixel_ratio == dpr
            assert request.render_spec.decoder_maximum_size is not None
            serial = window.presentation_state.committed_frame_serial
            window._on_zoom_changed(1.0)
            _wait_until(qapp, lambda: window.presentation_state.committed_frame_serial > serial)
            assert window._zip_runtime_request(window.model.spread_at()).render_spec.decoder_maximum_size is None
            assert not window.viewer._images[0].source_is_preview
            assert window.viewer._images[0].qimage.height() == 2400
            assert backend.reads == ["0.jpg"]
