from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage

from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.file_preview import PreviewResult, PreviewSource
from app.preview_provider_registry import PreviewProviderRegistry
from app.thumbnail_render import ThumbnailRenderSpec
from app.thumbnail_provider import BrowserThumbnailProvider
from app.windows_shell_preview import WindowsShellPreviewService
import app.windows_shell_preview as windows_shell_preview


def _item(path: Path) -> BrowserItem:
    stat = path.stat()
    return BrowserItem(
        path.name,
        path,
        BrowserItemKind.OTHER,
        stat.st_mtime,
        file_size=stat.st_size,
        modified_time_ns=stat.st_mtime_ns,
        extension=path.suffix,
        can_generate_preview=True,
        preview_kind="video",
    )


def _spec() -> ThumbnailRenderSpec:
    return ThumbnailRenderSpec.from_settings(64, "square_1_1", "letterbox")


def test_shell_cache_key_includes_windows_association_fingerprint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "movie.mp4"
    path.write_bytes(b"video")

    class Adapter:
        def __init__(self) -> None:
            self.calls = 0

        def get_image(self, _path, width, height, _flags):
            self.calls += 1
            image = QImage(width, height, QImage.Format.Format_RGBA8888)
            image.fill(0xFF224466)
            return image

    fingerprints = iter((
        (".mp4", "player-a", "player-a.exe"),
        (".mp4", "player-a", "player-a.exe"),
        (".mp4", "player-b", "player-b.exe"),
    ))
    monkeypatch.setattr(
        windows_shell_preview,
        "_windows_association_fingerprint",
        lambda _path: next(fingerprints),
    )
    adapter = Adapter()
    service = WindowsShellPreviewService(adapter)
    try:
        first = service.request_thumbnail(
            path,
            _spec(),
            cache_only=False,
            source_size=5,
            source_mtime_ns=1,
        )
        second = service.request_thumbnail(
            path,
            _spec(),
            cache_only=False,
            source_size=5,
            source_mtime_ns=1,
        )
        third = service.request_thumbnail(
            path,
            _spec(),
            cache_only=False,
            source_size=5,
            source_mtime_ns=1,
        )
        assert first.ready and second.ready and third.ready
        assert adapter.calls == 2
    finally:
        assert service.shutdown(join_timeout=1)


def test_windows_shell_visible_result_survives_viewer_pause_in_browser_cache(
    tmp_path: Path,
    qapp,
) -> None:
    path = tmp_path / "movie.mp4"
    path.write_bytes(b"video")
    item = _item(path)

    class Shell:
        def __init__(self) -> None:
            self.calls = 0

        def request_thumbnail(self, _path, spec, **_kwargs):
            self.calls += 1
            image = QImage(
                spec.frame_width,
                spec.frame_height,
                QImage.Format.Format_RGBA8888,
            )
            image.fill(0xFF224466)
            return PreviewResult.ready_image(
                image,
                source=PreviewSource.WINDOWS_SHELL,
                persist_to_disk=False,
            )

        def clear_memory_cache(self) -> None:
            pass

        def shutdown(self) -> None:
            pass

    shell = Shell()
    registry = PreviewProviderRegistry(
        settings={
            "video_thumbnail_enabled": True,
            "video_thumbnail_backend": "windows_shell",
            "video_thumbnail_frame_mode": "windows_shell",
        },
        shell_service=shell,  # type: ignore[arg-type]
    )
    provider = BrowserThumbnailProvider(
        disk_cache_enabled=False,
        preview_registry=registry,
    )
    spec = _spec()
    generation = provider.begin_generation()
    try:
        assert provider.request(
            item,
            spec,
            generation=generation,
            priority=ThumbnailPriority.PREFETCH,
        )
        assert provider.wait_for_done(3000)
        qapp.processEvents()
        assert shell.calls == 0
        assert provider.request(
            item,
            spec,
            generation=generation,
            priority=ThumbnailPriority.PREFETCH,
        )
        assert provider.wait_for_done(3000)
        qapp.processEvents()
        assert shell.calls == 0
        assert not provider.has_memory_thumbnail(item, spec)
        assert provider.memory_cache_bytes == 0

        assert provider.request(
            item,
            spec,
            generation=generation,
            priority=ThumbnailPriority.VISIBLE,
        )
        assert provider.wait_for_done(3000)
        qapp.processEvents()
        assert shell.calls == 1
        assert provider.has_memory_thumbnail(item, spec)
        assert provider.memory_cache_bytes > 0

        provider.set_paused(True)
        provider.set_paused(False)
        assert not provider.request(
            item,
            spec,
            generation=generation,
            priority=ThumbnailPriority.VISIBLE,
        )
        qapp.processEvents()
        assert shell.calls == 1
    finally:
        provider.close(wait_msecs=1000)
        qapp.processEvents()


def test_explicit_shell_invalidation_reaches_visible_request_after_association_change(
    tmp_path: Path,
    qapp,
    monkeypatch,
) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(folder))

    class RecordingProvider(BrowserThumbnailProvider):
        def __init__(self) -> None:
            super().__init__(disk_cache_enabled=False)
            self.requests: list[Path] = []

        def request(self, item, _size, **_kwargs):
            self.requests.append(item.path)
            return True

    provider = RecordingProvider()
    window = BrowserWindow(config_manager=config, thumbnail_provider=provider)
    window.resize(640, 420)
    window.show()
    assert window.wait_for_scan()
    qapp.processEvents()
    path = folder / "movie.mp4"
    path.write_bytes(b"video")
    item = BrowserItem(
        path.name,
        path,
        BrowserItemKind.OTHER,
        path.stat().st_mtime,
        file_size=path.stat().st_size,
        modified_time_ns=path.stat().st_mtime_ns,
        extension=".mp4",
        preview_kind="video",
    )
    window.item_model.set_items([item])
    monkeypatch.setattr(window, "_visible_row_range", lambda: (0, 0))
    first = QImage(32, 32, QImage.Format.Format_RGBA8888)
    first.fill(0xFF224466)
    assert window.item_model.set_thumbnail_image(
        path,
        first,
        request_token=window.thumbnail_render_spec.cache_token,
    )
    provider.requests.clear()

    window._request_visible_thumbnails()
    assert provider.requests == []

    assert window.invalidate_windows_shell_thumbnails() == 1
    window._request_visible_thumbnails()
    assert provider.requests == [path]
    second = QImage(32, 32, QImage.Format.Format_RGBA8888)
    second.fill(0xFF6688AA)
    window._on_thumbnail_ready(str(path), window._generation, second)
    stored = window.item_model.data(
        window.item_model.index(0, 0),
        BrowserItemModel.ThumbnailImageRole,
    )
    assert stored is not None
    assert stored.pixelColor(0, 0) == second.pixelColor(0, 0)
    window.close()
    qapp.processEvents()
