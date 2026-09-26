from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QImage

from app import cloud_files
from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_scanner import scan_entry_from_dir_entry
from app.file_preview import PreviewResultKind
from app.image_source import FolderImageSource, ImageSourceError, create_image_source
from app.path_availability import PathAvailability, PathAvailabilityFileSystem
from app.thumbnail_disk_cache import ThumbnailDiskCache, _content_signature
from app.thumbnail_provider import BrowserThumbnailProvider


@pytest.mark.parametrize('attribute', [0x1000, 0x40000, 0x400000])
def test_recall_flags_block_but_unpinned_local_data_does_not(attribute):
    assert cloud_files.requires_download(attribute)
    assert not cloud_files.requires_download(0x100000 | 0x400)


def mark_cloud(monkeypatch, *paths):
    blocked = {Path(p) for p in paths}
    monkeypatch.setattr(cloud_files, 'is_online_only', lambda p: Path(p) in blocked)


@pytest.mark.parametrize('suffix', ['.png', '.zip', '.pdf'])
def test_direct_open_stops_before_source_creation(tmp_path, monkeypatch, suffix):
    path = tmp_path / ('online' + suffix)
    path.touch()
    mark_cloud(monkeypatch, path)
    with pytest.raises(ImageSourceError, match='オンラインのみ') as error:
        create_image_source(path)
    assert error.value.code == 'online_only'


def test_folder_snapshot_and_recursive_listing_skip_cloud(tmp_path, monkeypatch):
    local = tmp_path / 'local.png'
    online = tmp_path / 'online.png'
    directory = tmp_path / 'online_folder'
    directory.mkdir()
    for path in (local, online, directory / 'hidden.png'):
        path.touch()
    mark_cloud(monkeypatch, online, directory)
    assert FolderImageSource(tmp_path, recursive=True).list_images() == [str(local)]
    source = FolderImageSource(tmp_path, image_snapshot=(str(online), str(local)))
    assert source.list_images() == [str(local)]
    # A file may become online-only after enumeration.
    mark_cloud(monkeypatch, local)
    with pytest.raises(cloud_files.OnlineOnlyError):
        source.probe_image_size(str(local))
    assert BrowserThumbnailProvider._folder_image_candidates(tmp_path) == [online]


@pytest.mark.parametrize('kind,suffix', [
    (BrowserItemKind.IMAGE, '.png'), (BrowserItemKind.ARCHIVE, '.zip'),
    (BrowserItemKind.PDF, '.pdf'), (BrowserItemKind.OTHER, '.mp4'),
    (BrowserItemKind.OTHER, '.txt'),
])
def test_preview_and_page_count_never_start_reader(tmp_path, monkeypatch, qapp, kind, suffix):
    path = tmp_path / ('online' + suffix)
    path.touch()
    item = BrowserItem(path.name, path, kind, 0)
    mark_cloud(monkeypatch, path)
    reader = Mock(side_effect=AssertionError('must not read source'))
    provider = BrowserThumbnailProvider(loader=reader)
    try:
        result = provider._load_pipeline(item, 128)
        assert result.resolved_kind is PreviewResultKind.UNAVAILABLE
        assert provider._load_page_count_pipeline(item, 128).resolved_kind is PreviewResultKind.UNAVAILABLE
        reader.assert_not_called()
        assert _content_signature(path) is None
    finally:
        provider.close()


def test_saved_preview_is_reused_without_source_content(tmp_path, monkeypatch, qapp):
    path = tmp_path / 'online.png'
    path.write_bytes(b'synthetic source')
    info = path.stat()
    item = BrowserItem(path.name, path, BrowserItemKind.IMAGE, info.st_mtime,
                       file_size=info.st_size, modified_time_ns=info.st_mtime_ns)
    cache = ThumbnailDiskCache(tmp_path / 'cache')
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(0)
    assert cache.put(item, 128, image)
    mark_cloud(monkeypatch, path)
    provider = BrowserThumbnailProvider(disk_cache=cache)
    try:
        result = provider._load_pipeline(item, 128)
        assert result.disk_cache_hit
        assert result.image is not None
    finally:
        provider.close()
        cache.close()


def test_scan_keeps_online_entry_with_cloud_marker(tmp_path):
    entry = SimpleNamespace(name='online.png', path=str(tmp_path / 'online.png'),
        stat=lambda **kw: SimpleNamespace(st_file_attributes=0x1000, st_mtime_ns=0, st_size=5),
        is_file=lambda **kw: True, is_dir=lambda **kw: False)
    result = scan_entry_from_dir_entry(entry)
    assert result is not None and result.online_only


def test_path_probe_blocks_external_open_and_recovers_after_download(tmp_path, monkeypatch):
    path = tmp_path / 'online.png'
    path.touch()
    mark_cloud(monkeypatch, path)
    assert PathAvailabilityFileSystem().probe(str(path)) is PathAvailability.ONLINE_ONLY
    mark_cloud(monkeypatch)
    assert PathAvailabilityFileSystem().probe(str(path)) is PathAvailability.AVAILABLE
