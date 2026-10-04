from __future__ import annotations

from pathlib import Path
import zipfile

import pytest
from PIL import Image
from PySide6.QtGui import QImage, QPixmap

from app.application_controller import ApplicationController
from app.browser_model import BrowserItemKind
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from test_application_controller import close_controller, wait_until
from test_browser_directory_watcher import FakeDirectoryWatcher, NoopThumbnailProvider, make_window


@pytest.mark.parametrize("selected_name", ["選択画像.png", "選択書庫.zip"])
def test_select_and_viewer_sync_keep_visible_video_thumbnails(
    tmp_path: Path, qapp, selected_name: str,
) -> None:
    folder = tmp_path / "一覧"
    folder.mkdir()
    image_path = folder / "選択画像.png"
    with Image.new("RGB", (32, 48), "red") as image:
        image.save(image_path)
    with zipfile.ZipFile(folder / "選択書庫.zip", "w") as archive:
        archive.write(image_path, "ページ.png")
    for number in range(3):
        (folder / f"動画{number}.mp4").write_bytes(b"video fixture")
    window, _watcher = make_window(tmp_path, folder, qapp)
    try:
        window.resize(1400, 900)
        for _ in range(5):
            qapp.processEvents()
        model = window.item_model
        token = window.thumbnail_render_spec.cache_token
        thumbnail = QImage(32, 32, QImage.Format.Format_RGB32)
        thumbnail.fill(0xFF2468AC)
        for item in window.items:
            model.set_thumbnail_image(item.path, thumbnail, request_token=token)
            if item.kind is BrowserItemKind.ARCHIVE:
                model.set_page_count(item.path, 1)
            elif item.kind is BrowserItemKind.IMAGE:
                model.set_image_dimensions(item.path, (32, 48))
        videos = [item for item in window.items if item.path.suffix == ".mp4"]
        first, last = window._visible_row_range()
        assert all(first <= model.row_for_path(item.path) <= last for item in videos)
        provider = window.thumbnail_provider
        requests = []
        provider.request = lambda item, *_args, **_kwargs: requests.append(str(item.path)) or False

        selected = folder / selected_name
        window.list_view.setCurrentIndex(model.index(model.row_for_path(selected), 0))
        # Check synchronously: an asynchronous RAM hit must not mask a blank frame.
        assert all(model._has_compatible_thumbnail(item, token) for item in videos)

        provider.set_paused(True)
        window.synchronize_viewer_item(selected, expected_parent=folder)
        provider.set_paused(False)
        for _ in range(5):
            qapp.processEvents()
        window._request_visible_thumbnails()
        assert all(model._has_compatible_thumbnail(item, token) for item in videos)
        assert not set(requests).intersection(str(item.path) for item in videos)
    finally:
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize("opened_name", ["画像.png", "書庫.zip"])
def test_real_offscreen_viewer_open_does_not_blank_or_rerequest_videos(
    tmp_path: Path, qapp, opened_name: str,
) -> None:
    folder = tmp_path / "一覧"
    folder.mkdir()
    with Image.new("RGB", (320, 480), "red") as image:
        image.save(folder / "画像.png")
    with zipfile.ZipFile(folder / "書庫.zip", "w") as archive:
        archive.write(folder / "画像.png", "ページ.png")
    for number in range(3):
        (folder / f"動画{number}.mp4").write_bytes(b"video fixture")
    provider = NoopThumbnailProvider()
    controller = ApplicationController(
        qapp, config_manager=ConfigManager(tmp_path / "config.json"),
        browser_window_factory=lambda **kwargs: BrowserWindow(
            **kwargs, thumbnail_provider=provider, directory_watcher=FakeDirectoryWatcher(),
        ),
    )
    try:
        window = controller.start()
        window.resize(1400, 900)
        assert window.navigate_to(folder)
        assert window.wait_for_scan()
        for _ in range(5):
            qapp.processEvents()
        model = window.item_model
        token = window.thumbnail_render_spec.cache_token
        thumbnail = QImage(32, 32, QImage.Format.Format_RGB32)
        thumbnail.fill(0xFF2468AC)
        for item in window.items:
            model.set_thumbnail_image(item.path, thumbnail, request_token=token)
        videos = [item for item in window.items if item.path.suffix == ".mp4"]
        requests = []
        provider.request = lambda item, *_args, **_kwargs: requests.append(str(item.path)) or False
        lost = []
        retain = model.retain_thumbnail_images

        def checked_retention(paths):
            result = retain(paths)
            lost.extend(item.path for item in videos if not model._has_compatible_thumbnail(item, token))
            return result

        model.retain_thumbnail_images = checked_retention
        window.open_item(model.index(model.row_for_path(folder / opened_name), 0))
        viewer = controller.get_active_viewer()
        assert viewer is not None
        assert viewer.book_session.wait_for_async(3000)
        assert wait_until(qapp, lambda: viewer.presentation_state.displayed_page == 0, timeout=5)
        canvas = QPixmap(viewer.viewer.size())
        canvas.fill()
        viewer.viewer.render(canvas)
        assert wait_until(qapp, lambda: not controller.image_work_coordinator.browser_paused, timeout=5)
        window._request_visible_thumbnails()
        assert not lost
        assert all(model._has_compatible_thumbnail(item, token) for item in videos)
        assert not set(requests).intersection(str(item.path) for item in videos)
    finally:
        close_controller(controller, qapp)
