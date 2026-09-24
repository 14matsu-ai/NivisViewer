from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Event, get_ident
from time import monotonic, sleep
import zipfile

from PIL import Image
from PySide6.QtCore import QByteArray
import pytest

from app.image_source import ZipImageSource
from app.zip_read_ahead import ZipReadAhead


def wait_until(qapp, predicate):
    end = monotonic() + 5
    while not predicate() and monotonic() < end:
        qapp.processEvents()
        sleep(0.001)
    assert predicate()


def make_source(tmp_path):
    path = tmp_path / "日本語.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index in range(4):
            encoded = BytesIO()
            with Image.new("RGB", (320, 480), (index * 30, 40, 90)) as image:
                image.save(encoded, format="JPEG")
            archive.writestr(f"{index}.jpg", encoded.getvalue())
    return ZipImageSource(path)


def test_only_one_payload_and_cancel_keeps_reservation_until_settled(qapp):
    started, release = Event(), Event()
    calls = []
    def read(image_id, cancelled):
        calls.append((image_id, get_ident()))
        started.set()
        assert release.wait(5)
        return QByteArray(b"test"), 1
    ahead = ZipReadAhead(read)
    completed = []
    ahead.signals.completed.connect(lambda: completed.append(True))
    try:
        ahead.configure({"0": ("1", 4)}, {"1"})
        ahead.kick("0")
        assert started.wait(2)
        ahead.cancel()
        ahead.configure({"2": ("3", 4)}, {"3"})
        ahead.kick("2")
        assert ahead.reserved_bytes == 4
        assert len(calls) == 1
        assert calls[0][1] != get_ident()
        release.set()
        wait_until(qapp, lambda: ahead.reserved_bytes == 0 and completed)
        assert ahead.take("1") is None
        ahead.kick("2")
        assert ahead.wait(2)
        assert bytes(ahead.take("3")[0]) == b"test"
        assert ahead.take("3") is None
        assert ahead.reserved_bytes == 0
    finally:
        release.set()
        ahead.close()


@pytest.mark.parametrize("failure", [False, True])
def test_zip_payload_consumed_once_or_retried_on_background_failure(tmp_path, qapp, failure):
    source = make_source(tmp_path)
    original = source._read_entry_qbytearray
    calls = []
    def read(image_id, cancelled):
        calls.append(image_id)
        if failure and len(calls) == 1:
            raise OSError("background read failed")
        return original(image_id, cancelled)
    source._read_entry_qbytearray = read
    try:
        source.configure_read_ahead(("0.jpg", "1.jpg"), 1024 * 1024)
        source._read_ahead.kick("0.jpg")
        assert source.wait_read_ahead(2)
        with ThreadPoolExecutor(1) as decoder:
            result = decoder.submit(source.open_compatible_jpeg_at_most, "1.jpg", (160, 240)).result(5)
        assert result is not None
        assert result.original_size == (320, 480)
        assert calls == ["1.jpg"] * (2 if failure else 1)
        assert source.read_ahead_reserved_bytes == 0
        assert source._active_requests == {}
    finally:
        source.close()


def test_budget_shrink_and_close_release_encoded_payload(tmp_path, qapp):
    source = make_source(tmp_path)
    try:
        source.configure_read_ahead(("0.jpg", "1.jpg"), 1)
        assert source._read_ahead is None
        source.configure_read_ahead(("0.jpg", "1.jpg"), 1024 * 1024)
        source._read_ahead.kick("0.jpg")
        assert source.wait_read_ahead(2)
        assert source.read_ahead_reserved_bytes > 0
        source.configure_read_ahead(("0.jpg", "1.jpg"), 1)
        assert source.read_ahead_reserved_bytes == 0
        source.close()
        source._read_ahead.kick("0.jpg")
        assert source.read_ahead_reserved_bytes == 0
        assert source._zip_closed
    finally:
        source.close()


def test_source_close_cancels_in_progress_reader_without_waiting(tmp_path, qapp):
    source = make_source(tmp_path)
    original = source._read_entry_qbytearray
    started, release = Event(), Event()
    tokens = []
    def read(image_id, cancelled):
        tokens.append(cancelled)
        started.set()
        assert release.wait(5)
        return original(image_id, cancelled)
    source._read_entry_qbytearray = read
    try:
        source.configure_read_ahead(("0.jpg", "1.jpg"), 1024 * 1024)
        source._read_ahead.kick("0.jpg")
        assert started.wait(2)
        source.close()
        assert tokens[0].is_set()
        assert not source._zip_closed
        release.set()
        wait_until(qapp, lambda: source._zip_closed and not source.read_ahead_busy)
        assert source._active_requests == {}
        assert source.read_ahead_reserved_bytes == 0
    finally:
        release.set()
        source.close()


@pytest.mark.parametrize("interruption", ["cancel", "reverse", "shrink", "suspend"])
def test_runtime_overlaps_read_with_one_decoder_and_notifies_idle_after_cancel(tmp_path, qapp, monkeypatch, interruption):
    from app import image_source
    from app.raster_warmup_planner import RasterBookTopology, RasterWarmupPlan
    from app.zip_raster_book_runtime import (
        ZipRasterBookRuntime, ZipRasterDisplayUnit, ZipRasterPage,
        ZipRasterRenderSpec, ZipRasterRequest,
    )
    source = make_source(tmp_path)
    units = tuple(ZipRasterDisplayUnit(i, (ZipRasterPage(i, f"{i}.jpg", (320, 480)),), True) for i in range(4))
    topology = RasterBookTopology(units, identity_of=lambda u: u.identity,
        page_indexes_of=lambda u: (p.page_index for p in u.pages), page_count=4)
    plan = RasterWarmupPlan(topology, current=units[0], identity_of=lambda u: u.identity,
        page_indexes_of=lambda u: (p.page_index for p in u.pages), direction=1, background_enabled=True)
    spec = ZipRasterRenderSpec((160, 240), decoder_maximum_size=(160, 240), decoder_layout_sized=True)
    runtime = ZipRasterBookRuntime(source, 1, zip_read_ahead=True)
    frames, idle, decoder_threads = [], [], []
    runtime.frameReady.connect(frames.append)
    runtime.idle.connect(lambda *_: idle.append(True))
    read_started, decode_started, release_read, release_decode = Event(), Event(), Event(), Event()
    original_read = source._read_entry_qbytearray
    original_decode = image_source._read_jpeg_qbytearray_at_most
    read_tokens = []
    def read(image_id, cancelled):
        if image_id == "2.jpg":
            read_tokens.append(cancelled)
            read_started.set()
            assert release_read.wait(5)
        return original_read(image_id, cancelled)
    def decode(*args):
        decoder_threads.append(get_ident())
        if len(decoder_threads) == 2:
            decode_started.set()
            assert read_started.wait(5)
            assert release_decode.wait(5)
        return original_decode(*args)
    source._read_entry_qbytearray = read
    monkeypatch.setattr(image_source, "_read_jpeg_qbytearray_at_most", decode)
    try:
        runtime.request(ZipRasterRequest(1, 1, units[0], plan, spec, navigation_direction=1))
        wait_until(qapp, lambda: bool(frames))
        assert source._read_ahead is None  # First frame takes precedence.
        runtime.release_continuous_warmup(request_id=1)
        wait_until(qapp, lambda: decode_started.is_set() and read_started.is_set())
        assert runtime.active_job_count == 1
        assert runtime.cache_bytes >= source.read_ahead_reserved_bytes > 0
        assert all(thread != get_ident() for thread in decoder_threads)
        if interruption == "reverse":
            runtime.request(ZipRasterRequest(1, 2, units[0], plan, spec, navigation_direction=-1))
        elif interruption == "shrink":
            runtime.set_memory_limits(hard_limit_bytes=256 * 1024 * 1024, soft_target_bytes=1)
        elif interruption == "suspend":
            runtime.suspend()
        if interruption != "cancel":
            assert read_tokens[0].is_set()
        frame_count = len(frames)
        runtime.cancel(clear_artifacts=True)
        idle.clear()
        release_decode.set()
        wait_until(qapp, lambda: not runtime._jobs)
        assert runtime.has_unfinished_tasks()
        assert not idle
        release_read.set()
        wait_until(qapp, lambda: not runtime.has_unfinished_tasks() and bool(idle))
        assert source.read_ahead_reserved_bytes == 0
        assert len(frames) == frame_count
        assert len(decoder_threads) == 2
    finally:
        release_decode.set()
        release_read.set()
        runtime.cancel(clear_artifacts=True)
        wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert runtime.shutdown()
        source.close()
