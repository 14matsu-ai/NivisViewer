"""Saved reads keep their correctness fences without serializing readers."""
from contextlib import contextmanager
from threading import Event, Thread

import pytest
from PIL import Image
from PySide6.QtGui import QImage, QImageReader

from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_render import ThumbnailRenderSpec
from tests.test_thumbnail_admission import source, pixels


@pytest.mark.parametrize('retire_generation', [False, True])
def test_metadata_rename_waits_for_only_target_source_handles(tmp_path, qapp, retire_generation):
    from app.thumbnail_provider import BrowserThumbnailProvider
    from threading import Timer
    items = [source(tmp_path / name) for name in ('target.png', 'other.png')]
    entered = [Event(), Event()]
    release = [Event(), Event()]
    def loader(item, _size, **_kwargs):
        index = 0 if item.path == items[0].path else 1
        with item.path.open('rb'):
            entered[index].set()
            assert release[index].wait(5)
            return pixels()
    provider = BrowserThumbnailProvider(loader=loader, max_workers=2, disk_cache_enabled=False)
    timer = None
    try:
        for item in items:
            assert provider.request(item, 96)
        assert all(event.wait(2) for event in entered)
        if retire_generation:
            provider.begin_generation()
        target = (str(items[0].path),)
        assert provider.release_file_handles(target, wait_msecs=0) is False
        timer = Timer(.05, release[0].set)
        timer.start()
        assert provider.release_file_handles(target, wait_msecs=1000)
        items[0].path.rename(tmp_path / 'renamed.png')
        assert not release[1].is_set()
    finally:
        for event in release:
            event.set()
        if timer is not None:
            timer.join()
        provider.close(wait_msecs=3000)
        qapp.processEvents()


@pytest.mark.parametrize('close', [False, True])
def test_index_read_does_not_hold_writer_lock_and_close_fences_result(tmp_path, close):
    item = source(tmp_path/'image.png')
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    cache = ThumbnailDiskCache(tmp_path/'cache')
    assert cache.put(item, spec, pixels())
    entered, release = Event(), Event()
    borrow = cache._borrow_read_connection
    connections, results, errors = [], [], []

    @contextmanager
    def blocked_reader():
        with borrow() as (connection, epoch):
            if not connections:
                connections.append(connection)
                def trace(sql):
                    if 'FROM entries WHERE source_path=' in sql:
                        entered.set()
                        release.wait(5)
                connection.set_trace_callback(trace)
            yield connection, epoch
    cache._borrow_read_connection = blocked_reader

    def read():
        try:
            results.append(cache.get_suitable(item, spec))
        except Exception as error:
            errors.append(error)
    thread = Thread(target=read)
    try:
        thread.start()
        assert entered.wait(2)
        assert cache._lock.acquire(timeout=.5)
        cache._lock.release()
        if close:
            cache.close()
        else:
            # Another read completes while the first SQLite SELECT is blocked.
            assert cache.get_suitable(item, spec) is not None
        release.set(); thread.join(5)
        assert not thread.is_alive() and not errors
        assert len(results) == 1 and (results[0] is None) == close
        assert not cache._read_leases
        if close:
            import sqlite3
            with pytest.raises(sqlite3.ProgrammingError):
                connections[0].execute('SELECT 1')
            assert not cache._read_connections
    finally:
        release.set(); thread.join(5); cache.close()


def test_commit_after_final_select_rechecks_shared_cache_revision(tmp_path):
    item = source(tmp_path/'image.png')
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    cache = ThumbnailDiskCache(tmp_path/'cache')
    other = ThumbnailDiskCache(tmp_path/'cache')
    assert cache.put(item, spec, pixels())
    borrow = cache._borrow_read_connection
    changed = []

    class Connection:
        def __init__(self, connection): self.connection = connection
        def execute(self, sql, args):
            cursor = self.connection.execute(sql, args)
            if sql.startswith('SELECT file_name,source_signature,cover_signature'):
                class Cursor:
                    def fetchone(self):
                        row = cursor.fetchone()
                        with other._lock:
                            rows = other._connection.execute('SELECT cache_key,file_name FROM entries').fetchall()
                            other._remove_entries(rows)
                        assert other.put(item, spec, pixels())
                        changed.append(True)
                        return row
                return Cursor()
            return cursor

    @contextmanager
    def race_reader():
        with borrow() as (connection, epoch):
            yield Connection(connection), epoch
    cache._borrow_read_connection = race_reader
    try:
        assert cache.get_suitable(item, spec) is None
        assert changed == [True] and not cache._read_leases
        assert other.get_suitable(item, spec) is not None
        assert len(list(other.files_dir.iterdir())) == 1
    finally:
        cache.close(); other.close()


@pytest.mark.parametrize('format', ['PNG', 'WEBP'])
@pytest.mark.parametrize('alpha', [False, True])
def test_saved_pixels_decode_directly_and_preserve_color_alpha_and_storage(tmp_path, monkeypatch, format, alpha):
    path = tmp_path/('saved.'+format.lower())
    color = (39, 95, 143, 101) if alpha else (39, 95, 143)
    with Image.new('RGBA' if alpha else 'RGB', (64, 48), color) as image:
        image.save(path, format=format, lossless=True)
    def no_python_image(*_args, **_kwargs):
        pytest.fail('Saved-image decoding must not round-trip through Pillow')
    monkeypatch.setattr(Image, 'open', no_python_image)
    monkeypatch.setattr(QImage, 'fromData', no_python_image)
    with path.open('rb') as handle:
        result = ThumbnailDiskCache._read_qimage(handle)
        assert not handle.closed
    assert result is not None and result.size().toTuple() == (64, 48)
    assert result.pixelColor(17, 23).getRgb() == (color if alpha else (*color, 255))
    # The result remains usable after its decoder and leased input are closed.
    path.unlink()
    assert result.sizeInBytes() <= 64*48*4
    assert result.pixelColor(17, 23).getRgb() == (color if alpha else (*color, 255))


def test_saved_read_without_qt_codec_and_corrupt_read_is_miss(tmp_path, monkeypatch):
    path = tmp_path/'saved.png'
    with Image.new('RGB', (32, 32), 'navy') as image:
        image.save(path)
    monkeypatch.setattr(QImageReader, 'read', lambda *_: QImage())
    assert ThumbnailDiskCache._read_qimage(path).pixelColor(0, 0).blue() == 128
    path.write_bytes(b'not an image')
    assert ThumbnailDiskCache._read_qimage(path) is None


def test_saved_read_queue_covers_one_viewport_with_bounded_worker_waves(qapp):
    from app.thumbnail_provider import BrowserThumbnailProvider
    from app.browser_generation_budget import browser_read_workers
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    workers = browser_read_workers()
    try:
        parallelism = provider._read_pool.maxThreadCount()
        provider.set_saved_read_window(workers*3)
        assert provider.background_read_request_limit == workers*3
        provider.set_saved_read_window(10_000_000)
        assert provider.background_read_request_limit == workers*4 <= 32
        assert provider._read_pool.maxThreadCount() == parallelism
        provider.set_saved_read_window(0)
        assert provider.background_read_request_limit == workers*2
    finally:
        provider.close()


def test_submission_does_not_join_pools_for_each_image(tmp_path, qapp, monkeypatch):
    from app.thumbnail_provider import BrowserThumbnailProvider
    entered, release = Event(), Event()
    def load(*_):
        entered.set(); release.wait(5)
        return pixels()
    provider = BrowserThumbnailProvider(disk_cache_enabled=False, loader=load, max_workers=1)
    calls = []
    original = provider._collect_owned_workers
    monkeypatch.setattr(provider, '_collect_owned_workers', lambda: calls.append(True))
    try:
        for i in range(8):
            assert provider.request(source(tmp_path/f'{i}.png'), 32, generation=provider.generation)
        assert entered.wait(2)
        assert not calls and provider._owned_collection_timer.isActive()
    finally:
        release.set()
        monkeypatch.setattr(provider, '_collect_owned_workers', original)
        provider.close(5000)
