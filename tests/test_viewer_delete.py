from __future__ import annotations

from pathlib import Path
from threading import Event
from time import monotonic, sleep

from PIL import Image
import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.file_operation_coordinator import FileOperationCoordinator
from app.file_operation_service import FileOperationService
from app.image_source import FolderListingSnapshot
from app.viewer_window import ViewerWindow
from app.viewer_delete_policy import select_delete_page
from app.windows_recycle_bin import RecycleBinResult


class FakeRecycleBin:
    def __init__(self, trash: Path) -> None:
        self.trash = trash
        trash.mkdir()
        self.paths: list[str] = []

    def recycle(self, path: str) -> RecycleBinResult:
        source = Path(path)
        self.paths.append(str(source))
        source.rename(self.trash / source.name)
        return RecycleBinResult(True)


class GatedRecycleBin(FakeRecycleBin):
    def __init__(self, trash: Path) -> None:
        super().__init__(trash)
        self.started = Event()
        self.release = Event()

    def recycle(self, path: str) -> RecycleBinResult:
        self.started.set()
        assert self.release.wait(5)
        return super().recycle(path)


class FailingRecycleBin(FakeRecycleBin):
    def recycle(self, path: str) -> RecycleBinResult:
        self.paths.append(str(path))
        return RecycleBinResult(False, error_code='shell_error', error_message='failed')


def test_delete_selection_uses_sort_order_and_never_guesses_cursor_side() -> None:
    assert select_delete_page((7,), 'single') == 7
    assert select_delete_page((7,), 'spread_back') == 7  # wide page in spread mode
    for visual_order in ((2, 3), (3, 2)):
        assert select_delete_page(visual_order, 'single') is None
        assert select_delete_page(visual_order, 'spread_front') == 2
        assert select_delete_page(visual_order, 'spread_back') == 3
        assert select_delete_page(visual_order, 'spread_cursor') is None
        assert select_delete_page(visual_order, 'spread_cursor', 3) == 3
    assert select_delete_page((2,), 'disabled') is None


def test_archive_pdf_refusal_and_auto_repeat_never_start_recycle(
    tmp_path: Path, qapp, monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    config.apply({'viewer_delete_mode': 'single'})
    session = BookSession()
    window = ViewerWindow(config_manager=config, book_session=session)
    notices = []
    monkeypatch.setattr('app.viewer_window.QMessageBox.information',
                        lambda _parent, _title, message, _buttons, _default:
                        notices.append(message) or QMessageBox.StandardButton.Ok)
    try:
        session.source = object()
        assert not window.delete_current_image_to_recycle_bin()
        assert '書庫' in notices[-1]
        class FakePdf:
            pass
        monkeypatch.setattr('app.viewer_window.PdfImageSource', FakePdf)
        session.source = FakePdf()
        assert not window.delete_current_image_to_recycle_bin()
        assert 'PDF' in notices[-1]
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete,
                          Qt.KeyboardModifier.NoModifier, '', True, 2)
        assert window._handle_viewer_delete_key(event)
        assert len(notices) == 2
    finally:
        session.source = None
        window.close()


def test_viewer_delete_uses_separate_settings_and_reloads_folder(
    tmp_path: Path, qapp,
) -> None:
    folder = tmp_path / 'images'
    folder.mkdir()
    for index in range(3):
        Image.new('RGB', (120, 180), 'navy').save(folder / f'{index:03d}.png')
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    config.apply({
        'viewer_delete_mode': 'single',
        'viewer_delete_skip_confirmation': True,
        'file_operation_delete_skip_confirmation': False,
    })
    recycle = FakeRecycleBin(tmp_path / 'trash')
    coordinator = FileOperationCoordinator(None, service=FileOperationService(recycle))
    session = BookSession()
    window = ViewerWindow(
        config_manager=config, book_session=session,
        file_operation_coordinator=coordinator,
    )
    recycled: list[str] = []
    window.file_recycled.connect(recycled.append)
    try:
        assert window.viewer_delete_shortcut.isEnabled()
        window.set_view_mode('single')
        window.show()
        qapp.processEvents()
        opened = session.open_book(folder)
        assert window._finish_opened_book(opened, modal_on_empty=False)
        deadline = monotonic() + 5
        while window.presentation_state.displayed is None and monotonic() < deadline:
            qapp.processEvents()
            sleep(0.01)
        assert window.presentation_state.displayed is not None
        session.generation += 1
        assert window._viewer_delete_page_index() is None
        session.generation -= 1
        QTest.keyClick(window.viewer, Qt.Key.Key_Delete)
        deadline = monotonic() + 5
        while not recycled and monotonic() < deadline:
            qapp.processEvents()
            sleep(0.01)
        assert [Path(path).name for path in recycle.paths] == ['000.png']
        assert recycled == recycle.paths
        assert config.get('file_operation_delete_skip_confirmation') is False
        deadline = monotonic() + 5
        while window.model.total_pages != 2 and monotonic() < deadline:
            qapp.processEvents()
            sleep(0.01)
        assert window.model.total_pages == 2
        config.apply({'viewer_delete_mode': 'disabled'})
        window.apply_settings({'viewer_delete_mode': 'disabled'})
        assert not window.viewer_delete_shortcut.isEnabled()
    finally:
        window.close()
        coordinator.close()


def test_late_delete_completion_does_not_replace_new_book(
    tmp_path: Path, qapp,
) -> None:
    folders = (tmp_path / 'first', tmp_path / 'second')
    for folder in folders:
        folder.mkdir()
        Image.new('RGB', (80, 120), 'navy').save(folder / '000.png')
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    config.apply({'viewer_delete_mode': 'single', 'viewer_delete_skip_confirmation': True})
    recycle = GatedRecycleBin(tmp_path / 'trash')
    coordinator = FileOperationCoordinator(None, service=FileOperationService(recycle))
    session = BookSession()
    window = ViewerWindow(config_manager=config, book_session=session,
                          file_operation_coordinator=coordinator)
    recycled: list[str] = []
    window.file_recycled.connect(recycled.append)
    try:
        window.show()
        assert window._finish_opened_book(session.open_book(folders[0]), modal_on_empty=False)
        deadline = monotonic() + 5
        while window.presentation_state.displayed is None and monotonic() < deadline:
            qapp.processEvents()
            sleep(0.01)
        assert window.delete_current_image_to_recycle_bin()
        assert recycle.started.wait(2)
        assert window._finish_opened_book(session.open_book(folders[1]), modal_on_empty=False)
        recycle.release.set()
        deadline = monotonic() + 5
        while window._pending_viewer_delete and monotonic() < deadline:
            qapp.processEvents()
            sleep(0.01)
        assert not window._pending_viewer_delete
        assert window._opened_path == str(folders[1])
        assert window.model.total_pages == 1
        assert recycled == recycle.paths
    finally:
        recycle.release.set()
        window.close()
        coordinator.close()


@pytest.mark.parametrize('use_snapshot', (False, True))
def test_delete_completion_keeps_page_navigated_to_while_recycle_was_running(
    tmp_path: Path, qapp, use_snapshot: bool,
) -> None:
    folder = tmp_path / 'images'
    folder.mkdir()
    paths = tuple(folder / f'{index:03d}.png' for index in range(3))
    for path in paths:
        Image.new('RGB', (80, 120), 'navy').save(path)
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    config.apply({'viewer_delete_mode': 'single', 'viewer_delete_skip_confirmation': True})
    recycle = GatedRecycleBin(tmp_path / 'trash')
    coordinator = FileOperationCoordinator(None, service=FileOperationService(recycle))
    session = BookSession()
    window = ViewerWindow(config_manager=config, book_session=session,
                          file_operation_coordinator=coordinator)
    try:
        window.set_view_mode('single')
        window.show()
        snapshot = (
            FolderListingSnapshot(folder, tuple(str(path) for path in paths), str(paths[0]))
            if use_snapshot else None
        )
        opened = session.open_book(
            paths[0] if use_snapshot else folder, folder_snapshot=snapshot,
        )
        assert window._finish_opened_book(opened, modal_on_empty=False)
        deadline = monotonic() + 5
        while window.presentation_state.displayed is None and monotonic() < deadline:
            qapp.processEvents()
            sleep(0.01)
        assert window.delete_current_image_to_recycle_bin()
        assert recycle.started.wait(2)
        window.next_page()
        assert window.model.image_id_at(window.model.focused_index) == str(paths[1])
        recycle.release.set()
        deadline = monotonic() + 5
        while (window._pending_viewer_delete is not None
               or window.model.total_pages != 2) and monotonic() < deadline:
            qapp.processEvents()
            sleep(0.01)
        assert window.model.total_pages == 2
        assert window.model.image_id_at(window.model.focused_index) == str(paths[1])
        assert recycle.paths == [str(paths[0])]
    finally:
        recycle.release.set()
        window.close()
        coordinator.close()


def test_failed_recycle_keeps_current_page_and_file(tmp_path: Path, qapp) -> None:
    folder = tmp_path / 'images'
    folder.mkdir()
    image_path = folder / '000.png'
    Image.new('RGB', (80, 120), 'navy').save(image_path)
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    config.apply({'viewer_delete_mode': 'single', 'viewer_delete_skip_confirmation': True})
    recycle = FailingRecycleBin(tmp_path / 'trash')
    coordinator = FileOperationCoordinator(None, service=FileOperationService(recycle))
    session = BookSession()
    window = ViewerWindow(config_manager=config, book_session=session,
                          file_operation_coordinator=coordinator)
    try:
        window.show()
        assert window._finish_opened_book(session.open_book(folder), modal_on_empty=False)
        deadline = monotonic() + 5
        while window.presentation_state.displayed is None and monotonic() < deadline:
            qapp.processEvents()
            sleep(0.01)
        assert window.delete_current_image_to_recycle_bin()
        deadline = monotonic() + 5
        while window._pending_viewer_delete and monotonic() < deadline:
            qapp.processEvents()
            sleep(0.01)
        assert window._pending_viewer_delete is None
        assert image_path.is_file()
        assert window.model.total_pages == 1
        assert window._opened_path == str(folder)
    finally:
        window.close()
        coordinator.close()


def test_recycle_preserves_browser_snapshot_order_and_spread_front(
    tmp_path: Path, qapp,
) -> None:
    folder = tmp_path / 'ordered'
    folder.mkdir()
    paths = tuple(str(folder / f'{name}.png') for name in ('c', 'a', 'd', 'b'))
    for path in paths:
        Image.new('RGB', (80, 120), 'navy').save(path)
    Image.new('RGB', (80, 120), 'navy').save(folder / 'excluded.png')
    snapshot = FolderListingSnapshot(
        folder, paths, paths[0], generation=7,
        fingerprints=tuple((path, 100, 1) for path in paths),
        sort_identity='browser-custom-order', selected_index=0,
        filter_identity='browser-filtered-items',
    )
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    config.apply({'viewer_delete_mode': 'spread_front',
                  'viewer_delete_skip_confirmation': True,
                  'single_first_page': False})
    recycle = FakeRecycleBin(tmp_path / 'trash')
    coordinator = FileOperationCoordinator(None, service=FileOperationService(recycle))
    session = BookSession()
    window = ViewerWindow(config_manager=config, book_session=session,
                          file_operation_coordinator=coordinator)
    try:
        window.set_view_mode('spread')
        window.show()
        opened = session.open_book(paths[0], folder_snapshot=snapshot)
        assert window._finish_opened_book(opened, modal_on_empty=False)
        deadline = monotonic() + 5
        while (window.presentation_state.displayed is None
               or len(window.presentation_state.displayed.unit.pages) != 2) and monotonic() < deadline:
            qapp.processEvents()
            sleep(0.01)
        assert set(page.index for page in window.presentation_state.displayed.unit.pages) == {0, 1}
        QTest.keyClick(window.viewer, Qt.Key.Key_Delete)
        expected = paths[1:]
        deadline = monotonic() + 5
        while tuple(window.model.image_ids) != expected and monotonic() < deadline:
            qapp.processEvents()
            sleep(0.01)
        assert recycle.paths == [paths[0]]
        assert tuple(window.model.image_ids) == expected
        retained = session.folder_listing_snapshot
        assert retained is not None
        assert retained.image_ids == expected
        assert tuple(entry[0] for entry in retained.fingerprints) == expected
        assert retained.sort_identity == snapshot.sort_identity
        assert retained.filter_identity == snapshot.filter_identity
        assert window.model.image_id_at(window.model.focused_index) == paths[1]
    finally:
        window.close()
        coordinator.close()


def test_recycle_last_filtered_image_does_not_reveal_excluded_files(
    tmp_path: Path, qapp,
) -> None:
    folder = tmp_path / 'filtered'
    folder.mkdir()
    visible = folder / 'visible.png'
    excluded = folder / 'excluded.png'
    for path in (visible, excluded):
        Image.new('RGB', (80, 120), 'navy').save(path)
    snapshot = FolderListingSnapshot(
        folder, (str(visible),), str(visible),
        filter_identity='only-visible',
    )
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    config.apply({'viewer_delete_mode': 'single', 'viewer_delete_skip_confirmation': True})
    recycle = FakeRecycleBin(tmp_path / 'trash')
    coordinator = FileOperationCoordinator(None, service=FileOperationService(recycle))
    session = BookSession()
    window = ViewerWindow(config_manager=config, book_session=session,
                          file_operation_coordinator=coordinator)
    try:
        window.show()
        assert window._finish_opened_book(
            session.open_book(visible, folder_snapshot=snapshot), modal_on_empty=False,
        )
        deadline = monotonic() + 5
        while window.presentation_state.displayed is None and monotonic() < deadline:
            qapp.processEvents()
            sleep(0.01)
        assert window.delete_current_image_to_recycle_bin()
        deadline = monotonic() + 5
        while window.model.total_pages and monotonic() < deadline:
            qapp.processEvents()
            sleep(0.01)
        assert window.model.total_pages == 0
        assert session.source is None
        assert excluded.exists()
        assert recycle.paths == [str(visible)]
    finally:
        window.close()
        coordinator.close()
