from contextlib import contextmanager
from io import BytesIO
from threading import Event
from zipfile import ZipFile

import pytest
from PIL import Image, ImageDraw
from PySide6.QtGui import QPixmap

from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.thumbnail_render import pil_to_qimage
from app.viewer_render import qimage_to_pillow
from app.viewer_window import ViewerWindow
from app.zip_raster_book_runtime import _ZipRasterUnitJob
from tests.test_zip_raster_viewer_integration import _wait_until


def _book(tmp_path, format, size, count):
    path = tmp_path / 'book.zip'
    with ZipFile(path, 'w') as archive:
        for index in range(count):
            with Image.new('RGB', size, (224, 192 - index * 29, 176)) as image:
                draw = ImageDraw.Draw(image)
                for x in range(3 + index, image.width - 3, 7):
                    draw.line((x, 3, x + 1, image.height - 4), fill='black')
                data = BytesIO()
                image.save(data, format=format, lossless=True)
                archive.writestr(f'{index:02d}.{format.lower()}', data.getvalue())
    return path


@contextmanager
def _opened(tmp_path, qapp, archive, *, spread=False):
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    config.apply({
        'view_mode': 'spread' if spread else 'single',
        'single_first_page': False,
        'fit_mode': 'fit_window',
        'show_page_list': False,
        'split_wide_image': False,
        'viewer_memory_mode': '256',
        'viewer_downscale_algorithm': 'auto',
        'viewer_upscale_algorithm': 'auto',
    })
    session = BookSession()
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(550, 450)
    window.prepare_initial_render_layout()
    commits = []
    window.presentationCommitted.connect(commits.append)
    try:
        window.open_path(archive)
        assert session.wait_for_async(10000)
        _wait_until(qapp, lambda: bool(commits), timeout_ms=5000)
        runtime = session.viewer_runtime
        assert window._zip_runtime_active and runtime is not None
        assert sorted(window.viewer.displayed_page_indexes) == list(range(2 if spread else 1))
        yield window, runtime, commits
        assert session.viewer_runtime is runtime
    finally:
        window.prepare_shutdown(wait_msecs=5000)
        window.close()
        qapp.processEvents()


def _images(window):
    return sorted(window.viewer._images, key=lambda image: image.page_index)


def _check_preview_pixels(window, archive, resampling):
    with ZipFile(archive) as book:
        for frame in _images(window):
            target = frame.pixmap.size().toTuple()
            assert frame.qimage.size().toTuple() == target
            with Image.open(BytesIO(book.read(book.namelist()[frame.page_index]))) as source:
                assert target[0] * 2 <= source.width and target[1] * 2 <= source.height
                with (
                    source.convert('RGBA' if 'transparency' in source.info else 'RGB') as pixels,
                    pixels.resize(target, resampling) as expected,
                ):
                    assert frame.pixmap.toImage() == QPixmap.fromImage(pil_to_qimage(expected)).toImage()


@pytest.mark.parametrize('format', ['PNG', 'WEBP'])
@pytest.mark.parametrize('spread', [False, True])
def test_live_downscale_switch_rebuilds_exact_size_previews(tmp_path, qapp, format, spread):
    book = _book(tmp_path, format, (801, 1203), 2 if spread else 1)
    with _opened(tmp_path, qapp, book, spread=spread) as (window, runtime, commits):
        _check_preview_pixels(window, book, Image.Resampling.BOX)
        before = _images(window)
        cached_keys = [image.qimage.cacheKey() for image in before]
        count = len(commits)
        window.config.apply({'viewer_downscale_algorithm': 'area'})
        _wait_until(qapp, lambda: len(commits) > count)
        # Auto at this ratio and area both use BOX on the existing pixels.
        assert [image.qimage.cacheKey() for image in _images(window)] == cached_keys

        before = _images(window)
        count = len(commits)
        window.viewer_downscale_actions['sharp'].trigger()
        assert [image.pixmap.cacheKey() for image in _images(window)] == [image.pixmap.cacheKey() for image in before]
        _wait_until(qapp, lambda: len(commits) > count)
        _check_preview_pixels(window, book, Image.Resampling.LANCZOS)
        assert [image.page_index for image in _images(window)] == [image.page_index for image in before]
        assert all(new.pixmap.toImage() != old.pixmap.toImage() for new, old in zip(_images(window), before))
        assert runtime.cache_bytes <= runtime.cache_byte_budget


def test_live_downscale_filter_applies_to_transparent_indexed_png(tmp_path, qapp):
    book = tmp_path / 'indexed.zip'
    data = BytesIO()
    with Image.new('P', (801, 1203), 1) as pixels:
        pixels.putpalette([25, 60, 90, 240, 220, 140])
        for x in range(3, pixels.width, 7):
            pixels.paste(0, (x, 0, x + 1, pixels.height))
        pixels.save(data, 'PNG', transparency=0)
    with ZipFile(book, 'w') as archive:
        archive.writestr('0.png', data.getvalue())

    with _opened(tmp_path, qapp, book) as (window, _runtime, commits):
        _check_preview_pixels(window, book, Image.Resampling.BOX)
        before, = _images(window)
        count = len(commits)
        window.set_viewer_downscale_algorithm('sharp')
        _wait_until(qapp, lambda: len(commits) > count)
        _check_preview_pixels(window, book, Image.Resampling.LANCZOS)
        after, = _images(window)
        assert before.pixmap.toImage() != after.pixmap.toImage()


@pytest.mark.parametrize('upscale', [False, True])
def test_live_filter_change_reuses_jpeg_source(tmp_path, qapp, upscale):
    size = (37, 59) if upscale else (801, 1203)
    book = _book(tmp_path, 'JPEG', size, 1)
    with _opened(tmp_path, qapp, book) as (window, _runtime, commits):
        before, = _images(window)
        before_source = before.qimage.size().toTuple()
        target = before.pixmap.size().toTuple()
        if upscale:
            assert before_source == size and target[1] > size[1]
        else:
            assert target[1] < before_source[1] < size[1]
        count = len(commits)
        if upscale:
            window.viewer_upscale_actions['nearest'].trigger()
        else:
            window.set_viewer_downscale_algorithm('sharp')
        _wait_until(qapp, lambda: len(commits) > count)
        after, = _images(window)
        assert before.qimage.cacheKey() == after.qimage.cacheKey()
        with qimage_to_pillow(before.qimage) as source:
            resampling = Image.Resampling.NEAREST if upscale else Image.Resampling.LANCZOS
            with source.resize(target, resampling) as expected:
                assert after.pixmap.toImage() == QPixmap.fromImage(pil_to_qimage(expected)).toImage()
        if upscale:
            assert before.pixmap.toImage() != after.pixmap.toImage()


def test_rapid_switch_and_navigation_ignore_old_completion_and_prefetch(tmp_path, qapp, monkeypatch):
    book = _book(tmp_path, 'PNG', (801, 1203), 4)
    entered, release = Event(), Event()
    make_result = _ZipRasterUnitJob._make_result

    def gate_result(job, *args, **kwargs):
        result = make_result(job, *args, **kwargs)
        if result.pages and job.key.render_spec.downscale_algorithm == 'smooth':
            entered.set()
            release.wait(8)
        return result

    monkeypatch.setattr(_ZipRasterUnitJob, '_make_result', gate_result)
    with _opened(tmp_path, qapp, book, spread=True) as (window, runtime, commits):
        try:
            _wait_until(qapp, lambda: {2, 3} <= set(runtime.cached_page_indexes), timeout_ms=6000)
            assert {2, 3} <= {source.page_index for source in runtime._source_store._sources.values()
                             if source.key.preview_resampling == Image.Resampling.BOX}
            frames = []
            runtime.frameReady.connect(frames.append)
            old_count = len(commits)
            stale = runtime.metrics.stale_results
            window.set_viewer_downscale_algorithm('smooth')
            _wait_until(qapp, entered.is_set)
            window.set_viewer_downscale_algorithm('fast')
            window.set_viewer_downscale_algorithm('sharp')
            window.next_page()
            assert sorted(window.viewer.displayed_page_indexes) == [0, 1]
            assert len(commits) == old_count
            release.set()
            _wait_until(qapp, lambda: sorted(window.viewer.displayed_page_indexes) == [2, 3], timeout_ms=6000)
            assert len(frames) == 1 and len(frames[0].pages) == 2
            assert runtime.metrics.stale_results > stale
            assert all(source.key.preview_resampling != Image.Resampling.BICUBIC
                       for source in runtime._source_store._sources.values())
            _check_preview_pixels(window, book, Image.Resampling.LANCZOS)
            assert runtime.cache_bytes <= runtime.cache_byte_budget
        finally:
            release.set()
