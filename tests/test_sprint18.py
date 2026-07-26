from __future__ import annotations

from io import BytesIO
from pathlib import Path
from threading import Event, get_ident
import subprocess
import time

import pytest
from PIL import Image
from PySide6.QtCore import QModelIndex, QPoint, QPointF, Qt, QObject, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from app.browser_main_drop import (
    BrowserMainDropController,
    PendingBrowserFocusRequest,
)
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_scanner import (
    BrowserScanBatch,
    BrowserScanCompleted,
    BrowserScanEntry,
    BrowserScanRequest,
    scan_entry_from_dir_entry,
)
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.browser_window import BrowserWindow
from app.browser_visibility import BrowserVisibilityPolicy
from app.config_manager import ConfigManager
from app.drag_drop import build_path_mime_data
from app.ffmpeg_thumbnail_backend import FFmpegThumbnailBackend
from app.file_preview import (
    QUIET_PREVIEW_RESULTS,
    PreviewResult,
    PreviewResultKind,
    PreviewSource,
)
from app.preview_provider_registry import (
    BrowserPreviewKind,
    PreviewProviderRegistry,
)
from app.shell_icon_provider import (
    ShellAssociatedIconProvider,
    physical_icon_bucket,
)
from app.settings_dialog import SettingsDialog
from app.text_preview_provider import TextPreviewProvider
from app.thumbnail_provider import BrowserThumbnailProvider, ThumbnailLoadResult
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_render import ThumbnailRenderSpec
from app.viewer_window import ViewerWindow
from app.windows_shell_preview import (
    SIIGBF_INCACHEONLY,
    SIIGBF_THUMBNAILONLY,
    WindowsShellPreviewService,
)


def _config(tmp_path: Path) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    return config


def _item(path: Path, kind: BrowserItemKind = BrowserItemKind.OTHER) -> BrowserItem:
    stat = path.stat() if path.exists() else None
    return BrowserItem(
        path.name,
        path,
        kind,
        stat.st_mtime if stat is not None else None,
        file_size=stat.st_size if stat is not None and path.is_file() else None,
        modified_time_ns=stat.st_mtime_ns if stat is not None else None,
        extension=path.suffix.casefold(),
        openable_by_nivisviewer=kind is not BrowserItemKind.OTHER,
        preview_kind="windows_shell" if kind is BrowserItemKind.OTHER else kind.value,
    )


def _spec(size: int = 128) -> ThumbnailRenderSpec:
    return ThumbnailRenderSpec.from_settings(
        size,
        "portrait_1_sqrt2",
        "letterbox",
    )


def _wait_until(qapp, predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        QTest.qWait(2)
    return bool(predicate())


def test_preview_result_kinds_keep_only_failed_non_quiet() -> None:
    assert PreviewResultKind.FAILED not in QUIET_PREVIEW_RESULTS
    assert {
        PreviewResultKind.NO_CONTENT,
        PreviewResultKind.NOT_APPLICABLE,
        PreviewResultKind.UNAVAILABLE,
        PreviewResultKind.CANCELLED,
    }.issubset(QUIET_PREVIEW_RESULTS)
    assert PreviewResult(PreviewResultKind.NO_CONTENT).quiet
    assert not PreviewResult.failed("broken").quiet


def test_empty_folder_is_quiet_and_not_decoded_repeatedly(
    tmp_path: Path,
    qapp,
    monkeypatch,
) -> None:
    folder = tmp_path / "画像なし"
    folder.mkdir()
    item = _item(folder, BrowserItemKind.FOLDER)
    calls: list[bool] = []

    def no_content(*_args, **_kwargs):
        calls.append(True)
        return ThumbnailLoadResult(
            None,
            result_kind=PreviewResultKind.NO_CONTENT,
            persist_to_disk=False,
        )

    monkeypatch.setattr(
        BrowserThumbnailProvider,
        "load_thumbnail_result",
        no_content,
    )
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    failures = QSignalSpy(provider.thumbnail_failed)
    states = QSignalSpy(provider.preview_state_changed)
    generation = provider.begin_generation()
    assert provider.request(item, _spec(), generation=generation)
    assert provider.wait_for_done(2000)
    qapp.processEvents()
    assert provider.request(item, _spec(), generation=generation)
    assert provider.wait_for_done(2000)
    qapp.processEvents()
    assert calls == [True]
    assert failures.count() == 0
    assert any(
        states.at(index)[2] == PreviewResultKind.NO_CONTENT.value
        for index in range(states.count())
    )
    provider.close()


@pytest.mark.parametrize(
    ("encoding", "payload"),
    [
        ("utf-8-sig", "日本語 UTF-8"),
        ("utf-16-le", "\ufeff日本語 UTF-16LE"),
        ("utf-16-be", "\ufeff日本語 UTF-16BE"),
        ("utf-32-le", "\ufeff日本語 UTF-32LE"),
        ("utf-32-be", "\ufeff日本語 UTF-32BE"),
        ("cp932", "日本語 CP932"),
    ],
)
def test_text_preview_decodes_windows_encodings(
    tmp_path: Path,
    encoding: str,
    payload: str,
) -> None:
    path = tmp_path / "日本語.txt"
    path.write_bytes(payload.encode(encoding))
    result = TextPreviewProvider().load_content(path)
    assert not isinstance(result, PreviewResult)
    assert "日本語" in result.text


def test_text_preview_empty_binary_and_bounded_render(tmp_path: Path) -> None:
    provider = TextPreviewProvider()
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    assert provider.load_content(empty).kind is PreviewResultKind.NO_CONTENT
    binary = tmp_path / "binary.txt"
    binary.write_bytes(b"abc\x00def")
    assert provider.load_content(binary).kind is PreviewResultKind.NOT_APPLICABLE
    source = tmp_path / "safe.html"
    source.write_text(
        "<script>alert('never executed')</script>\n" + "X" * 5000,
        encoding="utf-8",
    )
    result = provider.generate(source, _spec())
    assert result.ready
    assert result.image.size().width() == _spec().frame_width
    assert result.entry_path == "text-v1"
    assert result.persist_to_disk


class _FakeShellAdapter:
    def __init__(self, image: QImage | None = None) -> None:
        self.image = image or QImage(64, 64, QImage.Format.Format_ARGB32)
        self.image.fill(Qt.GlobalColor.cyan)
        self.calls: list[tuple[str, int, int, int]] = []
        self.thread_ids: list[int] = []

    def get_image(self, path, width: int, height: int, flags: int):
        self.thread_ids.append(get_ident())
        self.calls.append((str(path), width, height, flags))
        return self.image.copy()


def test_shell_preview_uses_cache_only_flags_and_memory_cache(tmp_path: Path) -> None:
    path = tmp_path / "movie.mp4"
    path.write_bytes(b"video")
    adapter = _FakeShellAdapter()
    service = WindowsShellPreviewService(adapter)
    caller_thread = get_ident()
    first = service.request_thumbnail(
        path,
        _spec(),
        cache_only=True,
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
    assert first.ready and second.ready
    assert len(adapter.calls) == 1
    assert adapter.thread_ids == [adapter.thread_ids[0]]
    assert adapter.thread_ids[0] != caller_thread
    flags = adapter.calls[0][3]
    assert flags & SIIGBF_THUMBNAILONLY
    assert flags & SIIGBF_INCACHEONLY
    assert not first.persist_to_disk
    service.shutdown()


class _UnavailableShell:
    def __init__(self) -> None:
        self.calls: list[ThumbnailPriority] = []

    def request_thumbnail(self, _path, _spec, *, cache_only, **_kwargs):
        self.calls.append(
            ThumbnailPriority.PREFETCH
            if cache_only
            else ThumbnailPriority.VISIBLE
        )
        return PreviewResult(PreviewResultKind.UNAVAILABLE)

    def clear_memory_cache(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


class _ReadyFFmpeg:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, _path, spec, _cancel):
        self.calls += 1
        image = QImage(
            spec.frame_width,
            spec.frame_height,
            QImage.Format.Format_ARGB32,
        )
        image.fill(Qt.GlobalColor.green)
        return PreviewResult.ready_image(
            image,
            source=PreviewSource.FFMPEG,
            persist_to_disk=True,
            entry_path="ffmpeg-v1",
        )


def test_registry_separates_open_capability_and_preview_pipeline(tmp_path: Path) -> None:
    text = tmp_path / "notes.txt"
    text.write_text("hello", encoding="utf-8")
    video = tmp_path / "movie.mkv"
    video.write_bytes(b"video")
    shell = _UnavailableShell()
    ffmpeg = _ReadyFFmpeg()
    registry = PreviewProviderRegistry(
        settings={"video_thumbnail_backend": "auto"},
        shell_service=shell,  # type: ignore[arg-type]
        ffmpeg_backend=ffmpeg,  # type: ignore[arg-type]
    )
    text_capability = registry.capability_for(_item(text))
    assert not text_capability.can_open
    assert text_capability.can_generate_preview
    assert text_capability.preview_kind is BrowserPreviewKind.TEXT
    assert registry.generate(
        _item(text),
        _spec(),
        priority=ThumbnailPriority.VISIBLE,
    ).ready
    video_result = registry.generate(
        _item(video),
        _spec(),
        priority=ThumbnailPriority.SELECTED,
    )
    assert video_result.ready
    assert video_result.source is PreviewSource.FFMPEG
    assert ffmpeg.calls == 1
    prefetch = registry.generate(
        _item(video),
        _spec(),
        priority=ThumbnailPriority.PREFETCH,
    )
    assert prefetch.kind is PreviewResultKind.UNAVAILABLE
    assert ffmpeg.calls == 1


class _FakeProcess:
    def __init__(self, png: bytes) -> None:
        self.returncode: int | None = None
        self._png = png
        self.terminated = False

    def communicate(self, timeout=None):
        self.returncode = 0
        return self._png, b""

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -1

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_ffmpeg_invocation_is_argument_safe_and_bounded(tmp_path: Path) -> None:
    executable = tmp_path / "ffmpeg.exe"
    executable.write_bytes(b"fake")
    source = tmp_path / "a & b; $(unsafe).mp4"
    source.write_bytes(b"fake video")
    output = BytesIO()
    Image.new("RGB", (80, 45), "blue").save(output, "PNG")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def factory(command, **kwargs):
        calls.append((command, kwargs))
        return _FakeProcess(output.getvalue())

    backend = FFmpegThumbnailBackend(executable, process_factory=factory)
    result = backend.generate(source, _spec())
    assert result.ready
    command, kwargs = calls[0]
    assert str(source) in command
    assert kwargs["shell"] is False
    assert "-nostdin" in command
    assert command[0] == str(executable)


class _NeverCompletesProcess(_FakeProcess):
    def communicate(self, timeout=None):
        raise subprocess.TimeoutExpired("ffmpeg", timeout)


def test_ffmpeg_timeout_terminates_child_without_warning_state(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "ffmpeg.exe"
    executable.write_bytes(b"fake")
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"video")
    process = _NeverCompletesProcess(b"")
    backend = FFmpegThumbnailBackend(
        executable,
        timeout_seconds=0.05,
        process_factory=lambda *_args, **_kwargs: process,
    )
    result = backend.generate(source, _spec())
    assert result.kind is PreviewResultKind.UNAVAILABLE
    assert process.terminated
    assert process.poll() is not None


def test_shell_icon_cache_uses_physical_dpr_buckets(monkeypatch) -> None:
    calls: list[int] = []

    def fake(_extension: str, *, folder: bool, physical_size: int) -> QImage:
        calls.append(physical_size)
        image = QImage(
            physical_size,
            physical_size,
            QImage.Format.Format_ARGB32,
        )
        image.fill(Qt.GlobalColor.red)
        return image

    monkeypatch.setattr(
        ShellAssociatedIconProvider,
        "_windows_image",
        staticmethod(fake),
    )
    provider = ShellAssociatedIconProvider()
    first = provider.image_for_extension(
        ".txt",
        logical_size=16,
        device_pixel_ratio=2.0,
    )
    second = provider.image_for_extension(
        ".TXT",
        logical_size=16,
        device_pixel_ratio=2.0,
    )
    larger = provider.image_for_extension(
        ".txt",
        logical_size=16,
        device_pixel_ratio=3.0,
    )
    assert first.width() == physical_icon_bucket(16, 2.0)
    assert second.width() == first.width()
    assert larger.width() >= 48
    assert calls == [32, 48]


def test_config_defaults_and_normalizes_sprint18_settings(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert config.get("text_preview_enabled") is True
    assert config.get("video_thumbnail_enabled") is True
    assert config.get("video_thumbnail_backend") == "auto"
    assert config.get("browser_external_drop_behavior") == "focus_only"
    changed = config.apply(
        {
            "video_thumbnail_backend": "invalid",
            "browser_external_drop_behavior": "open-everything",
            "ffmpeg_executable": 123,
        }
    )
    assert config.get("video_thumbnail_backend") == "auto"
    assert config.get("browser_external_drop_behavior") == "focus_only"
    assert config.get("ffmpeg_executable") == ""
    assert changed == {}
    config.apply(
        {
            "text_preview_enabled": False,
            "video_thumbnail_backend": "windows_shell",
            "ffmpeg_executable": r"C:\Tools\ffmpeg.exe",
            "browser_external_drop_behavior": "focus_and_open",
        },
        save=True,
    )
    loaded = ConfigManager(config.path)
    loaded.load()
    assert loaded.get("text_preview_enabled") is False
    assert loaded.get("video_thumbnail_backend") == "windows_shell"
    assert loaded.get("ffmpeg_executable") == r"C:\Tools\ffmpeg.exe"
    assert loaded.get("browser_external_drop_behavior") == "focus_and_open"


def test_settings_dialog_round_trips_preview_controls(
    tmp_path: Path,
    qapp,
) -> None:
    config = _config(tmp_path)
    dialog = SettingsDialog(config)
    assert dialog.text_preview_checkbox.isChecked()
    assert dialog.video_thumbnail_checkbox.isChecked()
    dialog.text_preview_checkbox.setChecked(False)
    dialog.video_thumbnail_checkbox.setChecked(False)
    index = dialog.video_thumbnail_backend_combo.findData("ffmpeg")
    dialog.video_thumbnail_backend_combo.setCurrentIndex(index)
    dialog.ffmpeg_path_edit.setText(r"C:\Tools\ffmpeg.exe")
    drop_index = dialog.browser_external_drop_combo.findData("focus_and_open")
    dialog.browser_external_drop_combo.setCurrentIndex(drop_index)
    values = dialog.values()
    assert values["text_preview_enabled"] is False
    assert values["video_thumbnail_enabled"] is False
    assert values["video_thumbnail_backend"] == "ffmpeg"
    assert values["ffmpeg_executable"] == r"C:\Tools\ffmpeg.exe"
    assert values["browser_external_drop_behavior"] == "focus_and_open"
    dialog.reject()
    qapp.processEvents()


def test_settings_ffmpeg_redetect_runs_asynchronously(
    tmp_path: Path,
    qapp,
) -> None:
    executable = tmp_path / "ffmpeg.exe"
    executable.write_bytes(b"fake")

    class Locator:
        def locate(self, _path):
            return executable

    dialog = SettingsDialog(
        _config(tmp_path),
        ffmpeg_locator=Locator(),  # type: ignore[arg-type]
    )
    dialog.redetect_ffmpeg()
    assert "確認中" in dialog.ffmpeg_status_label.text()
    assert not dialog.ffmpeg_redetect_button.isEnabled()
    assert _wait_until(
        qapp,
        lambda: dialog.ffmpeg_redetect_button.isEnabled(),
    )
    assert str(executable) in dialog.ffmpeg_status_label.text()
    dialog.reject()
    qapp.processEvents()


def test_persistent_preview_cache_is_partitioned_by_renderer_version(
    tmp_path: Path,
) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("text", encoding="utf-8")
    item = _item(source)
    spec = _spec()
    cache = ThumbnailDiskCache(tmp_path / "cache")
    old = QImage(
        spec.frame_width,
        spec.frame_height,
        QImage.Format.Format_ARGB32,
    )
    old.fill(Qt.GlobalColor.red)
    current = QImage(
        spec.frame_width,
        spec.frame_height,
        QImage.Format.Format_ARGB32,
    )
    current.fill(Qt.GlobalColor.green)
    assert cache.put(item, spec, old, entry_path="text-v0")
    assert cache.put(item, spec, current, entry_path="text-v1")
    selected = cache.get_suitable(item, spec, entry_path="text-v1")
    assert selected is not None
    color = selected.image.pixelColor(0, 0)
    assert color.green() > 240 and color.red() < 10
    assert (
        cache.get_suitable(
            item,
            spec,
            entry_path="__preview_disabled__",
        )
        is None
    )
    cache.close()


def test_scanner_marks_text_and_video_preview_without_viewer_support(
    tmp_path: Path,
) -> None:
    text = tmp_path / "notes.txt"
    video = tmp_path / "movie.mp4"
    text.write_text("text", encoding="utf-8")
    video.write_bytes(b"video")
    with __import__("os").scandir(tmp_path) as entries:
        scanned = {
            entry.name: scan_entry_from_dir_entry(
                entry,
                BrowserVisibilityPolicy(show_unsupported_files=True),
            )
            for entry in entries
        }
    assert scanned["notes.txt"].preview_kind == "text"
    assert scanned["movie.mp4"].preview_kind == "video"
    assert not scanned["notes.txt"].openable_by_nivisviewer
    assert scanned["movie.mp4"].can_generate_preview


def test_browser_main_drop_groups_first_parent_asynchronously(
    tmp_path: Path,
    qapp,
) -> None:
    first_folder = tmp_path / "A"
    second_folder = tmp_path / "B"
    first_folder.mkdir()
    second_folder.mkdir()
    first = first_folder / "1.txt"
    second = first_folder / "2.txt"
    ignored = second_folder / "3.txt"
    for path in (first, second, ignored):
        path.write_text(path.name, encoding="utf-8")
    controller = BrowserMainDropController()
    results: list[PendingBrowserFocusRequest] = []
    controller.focus_request_ready.connect(results.append)
    request_id = controller.handle_external_paths(
        (str(first), str(second), str(ignored)),
        behavior="focus_and_open",
    )
    assert request_id > 0
    assert _wait_until(qapp, lambda: bool(results))
    request = results[0]
    assert request.folder == first_folder.absolute()
    assert request.paths == (first.absolute(), second.absolute())
    assert request.primary == first.absolute()
    assert request.open_after
    assert request.ignored_count == 1
    controller.close()


def test_browser_drop_focuses_same_folder_without_opening_viewer(
    tmp_path: Path,
    qapp,
) -> None:
    folder = tmp_path / "一覧"
    folder.mkdir()
    first = folder / "a.txt"
    second = folder / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    opened: list[str] = []
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
        open_path_handler=lambda path, *_args: opened.append(path),
    )
    window.current_path = folder.absolute()
    window.item_model.set_items([_item(first), _item(second)])
    request = PendingBrowserFocusRequest(
        folder.absolute(),
        (first.absolute(), second.absolute()),
        second.absolute(),
        window._scan_generation,
        False,
        window.browser_main_drop.active_request_id,
    )
    window._begin_browser_drop_focus(request)
    selected = {
        Path(index.data(BrowserItemModel.PathRole))
        for index in window.list_view.selectionModel().selectedIndexes()
    }
    assert selected == {first.absolute(), second.absolute()}
    assert (
        Path(window.list_view.currentIndex().data(BrowserItemModel.PathRole))
        == second.absolute()
    )
    assert opened == []
    assert not window.list_view.hasFocus()
    window.close()
    qapp.processEvents()


def test_browser_focus_and_open_only_opens_supported_primary(
    tmp_path: Path,
    qapp,
) -> None:
    folder = tmp_path / "open"
    folder.mkdir()
    image = folder / "page.jpg"
    Image.new("RGB", (16, 24), "white").save(image)
    unsupported = folder / "notes.txt"
    unsupported.write_text("text", encoding="utf-8")
    opened: list[str] = []
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
        open_path_handler=lambda path, *_args: opened.append(path),
    )
    window.current_path = folder.absolute()
    window.item_model.set_items(
        [_item(image, BrowserItemKind.IMAGE), _item(unsupported)]
    )
    request_id = window.browser_main_drop.active_request_id
    window._begin_browser_drop_focus(
        PendingBrowserFocusRequest(
            folder.absolute(),
            (image.absolute(),),
            image.absolute(),
            window._scan_generation,
            True,
            request_id,
        )
    )
    assert opened == [str(image.absolute())]
    window._begin_browser_drop_focus(
        PendingBrowserFocusRequest(
            folder.absolute(),
            (unsupported.absolute(),),
            unsupported.absolute(),
            window._scan_generation,
            True,
            request_id,
        )
    )
    assert opened == [str(image.absolute())]
    window.close()
    qapp.processEvents()


class _FakeScanner(QObject):
    batch_ready = Signal(object)
    scan_completed = Signal(object)
    scan_failed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[BrowserScanRequest] = []
        self.cancelled: list[int] = []

    def start(self, request: BrowserScanRequest) -> bool:
        self.requests.append(request)
        return True

    def cancel(self, generation: int) -> None:
        self.cancelled.append(generation)

    def close(self) -> None:
        pass


def test_browser_drop_focus_applies_incremental_batch_and_discards_stale(
    tmp_path: Path,
    qapp,
) -> None:
    folder = tmp_path / "target"
    other = tmp_path / "other"
    folder.mkdir()
    other.mkdir()
    target = folder / "target.txt"
    target.write_text("target", encoding="utf-8")
    scanner = _FakeScanner()
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        scanner=scanner,  # type: ignore[arg-type]
        restore_initial_location=False,
    )
    request = PendingBrowserFocusRequest(
        folder.absolute(),
        (target.absolute(),),
        target.absolute(),
        -1,
        False,
        window.browser_main_drop.active_request_id,
    )
    window._begin_browser_drop_focus(request)
    scan = scanner.requests[-1]
    scanner.batch_ready.emit(
        BrowserScanBatch(
            scan.path,
            scan.generation,
            (
                BrowserScanEntry(
                    str(target.absolute()),
                    target.name,
                    "other",
                    target.stat().st_mtime_ns,
                    target.stat().st_size,
                    extension=".txt",
                    openable_by_nivisviewer=False,
                    preview_kind="text",
                ),
            ),
        )
    )
    qapp.processEvents()
    assert window.item_model.row_for_path(target) >= 0
    assert window.list_view.currentIndex().isValid()
    assert window.navigate_to(other)
    assert window._pending_browser_focus is None
    scanner.scan_completed.emit(
        BrowserScanCompleted(scan.path, scan.generation, 1)
    )
    qapp.processEvents()
    assert window.current_path == folder.absolute()
    replacement = scanner.requests[-1]
    scanner.scan_completed.emit(
        BrowserScanCompleted(replacement.path, replacement.generation, 0)
    )
    qapp.processEvents()
    assert window.current_path == other.absolute()
    window.close()
    qapp.processEvents()


def test_same_folder_drop_waits_for_active_refresh_generation(
    tmp_path: Path,
    qapp,
) -> None:
    folder = tmp_path / "refresh"
    folder.mkdir()
    target = folder / "late.txt"
    target.write_text("late", encoding="utf-8")
    scanner = _FakeScanner()
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        scanner=scanner,  # type: ignore[arg-type]
        restore_initial_location=False,
    )
    window.current_path = folder.absolute()
    assert window.navigate_to(folder, force_reload=True)
    scan = scanner.requests[-1]
    window._begin_browser_drop_focus(
        PendingBrowserFocusRequest(
            folder.absolute(),
            (target.absolute(),),
            target.absolute(),
            -1,
            False,
            window.browser_main_drop.active_request_id,
        )
    )
    assert window._pending_browser_focus is not None
    assert not window.list_view.currentIndex().isValid()
    entry = BrowserScanEntry(
        str(target.absolute()),
        target.name,
        "other",
        target.stat().st_mtime_ns,
        target.stat().st_size,
        extension=".txt",
        openable_by_nivisviewer=False,
        preview_kind="text",
    )
    scanner.batch_ready.emit(
        BrowserScanBatch(scan.path, scan.generation, (entry,))
    )
    scanner.scan_completed.emit(
        BrowserScanCompleted(scan.path, scan.generation, 1)
    )
    qapp.processEvents()
    assert window._pending_browser_focus is None
    assert (
        Path(window.list_view.currentIndex().data(BrowserItemModel.PathRole))
        == target.absolute()
    )
    window.close()
    qapp.processEvents()


def test_real_qt_external_drop_reaches_browser_main_controller(
    tmp_path: Path,
    qapp,
    monkeypatch,
) -> None:
    path = tmp_path / "dropped.txt"
    path.write_text("drop", encoding="utf-8")
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
    )
    window.show()
    qapp.processEvents()
    calls: list[tuple[tuple[str, ...], str]] = []
    monkeypatch.setattr(
        window.browser_main_drop,
        "handle_external_paths",
        lambda paths, *, behavior: calls.append((tuple(paths), behavior)) or 1,
    )
    mime = build_path_mime_data((str(path.absolute()),))
    event = QDropEvent(
        QPointF(3, 3),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    enter = QDragEnterEvent(
        QPoint(3, 3),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.list_view.dragEnterEvent(enter)
    assert enter.isAccepted()
    window.list_view.dropEvent(event)
    assert calls == [((str(path.absolute()),), "focus_only")]
    window.close()
    qapp.processEvents()


def test_viewer_left_click_moves_one_logical_page_but_next_is_display_unit(
    tmp_path: Path,
    qapp,
) -> None:
    folder = tmp_path / "book"
    folder.mkdir()
    pages = []
    for index in range(6):
        path = folder / f"{index:02}.jpg"
        Image.new("RGB", (80, 120), "white").save(path)
        pages.append(path)
    config = _config(tmp_path)
    config.apply(
        {
            "view_mode": "spread",
            "single_first_page": True,
            "treat_wide_image_as_single": True,
        }
    )
    window = ViewerWindow(config_manager=config)
    assert window.open_path(pages[0])
    window.resize(640, 480)
    window.show()
    qapp.processEvents()
    window.activateWindow()
    window.viewer.setFocus()
    qapp.processEvents()
    window.next_page()
    assert window.model.current_index == 1
    QTest.mouseClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=QPoint(window.viewer.width() // 2, window.viewer.height() // 2),
    )
    assert window.viewer.canvas_pointer.pending
    QTest.qWait(QApplication.doubleClickInterval() + 250)
    assert window.model.current_index == 2
    assert window.model.focused_index == 2
    window.next_page()
    assert window.model.current_index == 4
    window.close()
    qapp.processEvents()
