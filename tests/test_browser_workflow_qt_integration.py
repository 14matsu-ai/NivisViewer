"""Requires the real project + PySide6; never uses native input or main.py."""
from __future__ import annotations
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from types import SimpleNamespace
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QMimeData, QUrl, QObject, QEvent, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor, QImage, QInputMethodEvent, QKeyEvent, QPainter, QPalette,
)
from PySide6.QtWidgets import (
    QMessageBox, QStyle, QStyleOptionViewItem, QWidget, QLineEdit,
)
from app.internal_clipboard import ClipboardPasteReceipt, InternalClipboardState
from app.browser_workflow_policy import paste_is_move
from app.browser_workflow_settings import BrowserWorkflowSettings
from app.browser_item_delegate import BrowserItemDelegate
from app.config_manager import ConfigManager
from app.file_operation_service import FileOperationService, FileOperationRequest, FileOperationKind
from app.file_operation_service import FileOperationItemResult, FileOperationResult
from app.shortcut_catalog import normalize_shortcut_bindings
from app.browser_workflow_controller import BrowserWorkflowController
from app.browser_workflow_policy import SelectionAppearance, ThumbnailWarmupCursor
from app.browser_window import BrowserWindow
from app.browser_model import BrowserItem, BrowserItemKind
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import ThumbnailRenderSpec
from PIL import Image


class _ClipboardCleanupHarness:
    def __init__(self):
        self._internal_clipboard_state = InternalClipboardState()

    @staticmethod
    def _path_key(path):
        return os.path.normcase(os.path.abspath(os.path.normpath(path))).casefold()

    _finish_paste_clipboard = BrowserWindow._finish_paste_clipboard


def test_external_cut_mime_is_move_without_private_marker(tmp_path):
    path=tmp_path/'page.jpg'; path.write_bytes(b'test')
    mime=QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(path))])
    mime.setData('application/x-qt-windows-mime;value="Preferred DropEffect"',b'\x02\0\0\0')
    state=InternalClipboardState()
    assert paste_is_move(internal_matches=state.matches_mime(mime),internal_cut=state.is_cut,
                         preferred_effect=state.preferred_drop_effect(mime))


def test_old_paste_does_not_clear_replaced_clipboard_with_same_urls(tmp_path, qapp):
    source=tmp_path/'page.jpg'
    clipboard=qapp.clipboard()
    first=QMimeData(); first.setUrls([QUrl.fromLocalFile(str(source))])
    first.setData('application/x-qt-windows-mime;value="Preferred DropEffect"',b'\x02\0\0\0')
    clipboard.setMimeData(first)
    state=InternalClipboardState()
    receipt=ClipboardPasteReceipt.capture(state,clipboard.mimeData())

    replacement=QMimeData(); replacement.setUrls([QUrl.fromLocalFile(str(source))])
    replacement.setData('application/x-qt-windows-mime;value="Preferred DropEffect"',b'\x02\0\0\0')
    clipboard.setMimeData(replacement)
    assert not receipt.matches(state,clipboard.mimeData())

    result=FileOperationResult(FileOperationKind.MOVE,(
        FileOperationItemResult(str(source),str(tmp_path/'dest'/'page.jpg'),True,
                                source_removed=True,source_exists_after=False),))
    _ClipboardCleanupHarness()._finish_paste_clipboard(result,receipt)
    assert [Path(url.toLocalFile()) for url in clipboard.mimeData().urls()]==[source]
    clipboard.clear()


def test_current_external_cut_is_cleared_after_successful_move(tmp_path, qapp):
    source=tmp_path/'page.jpg'
    clipboard=qapp.clipboard()
    mime=QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(source))])
    mime.setData('application/x-qt-windows-mime;value="Preferred DropEffect"',b'\x02\0\0\0')
    clipboard.setMimeData(mime)
    state=InternalClipboardState()
    receipt=ClipboardPasteReceipt.capture(state,clipboard.mimeData())
    result=FileOperationResult(FileOperationKind.MOVE,(
        FileOperationItemResult(str(source),str(tmp_path/'dest'/'page.jpg'),True,
                                source_removed=True,source_exists_after=False),))

    _ClipboardCleanupHarness()._finish_paste_clipboard(result,receipt)
    current=clipboard.mimeData()
    assert current is None or not current.hasUrls()


def test_browser_search_shortcuts_yield_during_ime_composition(qapp):
    from PySide6.QtWidgets import QLineEdit
    edit=QLineEdit()
    controller=BrowserWorkflowController.__new__(BrowserWorkflowController)
    QObject.__init__(controller)
    controller.window=SimpleNamespace(browser_search_edit=edit)
    controller._ime_composing=False

    assert not controller.handle_key(edit,QInputMethodEvent('にほんご',[]))
    assert controller._ime_composing
    shortcut=QKeyEvent(QEvent.Type.KeyPress,ord('F'),Qt.KeyboardModifier.ControlModifier)
    assert not controller.handle_key(edit,shortcut)
    assert not controller.handle_key(edit,QInputMethodEvent('',[]))
    assert not controller._ime_composing
    edit.deleteLater(); controller.deleteLater(); qapp.processEvents()


def test_rejected_background_submission_is_retryable_without_immediate_repump(qapp):
    controller = BrowserWorkflowController.__new__(BrowserWorkflowController)
    QObject.__init__(controller)
    controller._inflight = (3, "row-3.jpg", 7, 99, ("revision",), (7, 99, 2, 4))
    controller._cursor = ThumbnailWarmupCursor(10)
    controller._cursor.recenter(0, 1, 1, 4)
    assert controller._cursor.take() == 2
    assert controller._cursor.take() == 3
    scheduled = []
    controller.schedule_background = lambda: scheduled.append(True)

    controller._submission_rejected(
        "row-3.jpg", 7, 99, ("revision",)
    )

    assert controller._inflight is None
    assert controller._cursor.take() == 3
    assert scheduled == []
    controller.deleteLater()
    qapp.processEvents()


def test_large_undo_decline_keeps_receipt_and_later_accept_dispatches(
    qapp, monkeypatch
):
    entries = tuple(SimpleNamespace(path=f"file-{index}") for index in range(513))
    coordinator = SimpleNamespace(busy=False, undo_entries=entries)
    dispatches = []
    window = SimpleNamespace(
        file_operation_coordinator=coordinator,
        _rating_batch=None,
        _start_file_operation=lambda operation, **values: dispatches.append(
            (operation, values)
        ) or True,
    )
    controller = BrowserWorkflowController.__new__(BrowserWorkflowController)
    controller.window = window
    prompts = []

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda _parent, _title, text, _buttons, _default: (
            prompts.append(text), QMessageBox.StandardButton.No
        )[1],
    )
    assert controller.undo() is False
    assert coordinator.undo_entries is entries
    assert dispatches == []
    assert "513項目" in prompts[0]
    assert "スキップ" in prompts[0] and "一部だけ" in prompts[0]

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )
    assert controller.undo() is True
    assert len(dispatches) == 1
    operation, values = dispatches[0]
    assert operation is FileOperationKind.UNDO
    assert len(values["sources"]) == 513
    assert coordinator.undo_entries is entries


def test_undo_at_or_below_512_does_not_show_confirmation(qapp, monkeypatch):
    entries = tuple(SimpleNamespace(path=f"file-{index}") for index in range(512))
    coordinator = SimpleNamespace(busy=False, undo_entries=entries)
    dispatches = []
    controller = BrowserWorkflowController.__new__(BrowserWorkflowController)
    controller.window = SimpleNamespace(
        file_operation_coordinator=coordinator,
        _rating_batch=None,
        _start_file_operation=lambda operation, **values: dispatches.append(
            (operation, values)
        ) or True,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: pytest.fail("small undo must not request confirmation"),
    )

    assert controller.undo() is True
    assert len(dispatches) == 1
    assert len(dispatches[0][1]["sources"]) == 512


def test_workflow_settings_roundtrip(qapp):
    widget=BrowserWorkflowSettings()
    values={'browser_thumbnail_background_screens':-1,'browser_selection_filename_opacity':0,
            'browser_selection_border_width':9,'browser_selection_color':'#aabbcc',
            'browser_selection_text_color_auto_adjust':False,
            'browser_selection_frame_rounded':True}
    widget.load(values)
    assert widget.values()==values
    widget.load({})
    assert widget.values()['browser_thumbnail_background_screens']==3
    assert widget.values()['browser_selection_text_color_auto_adjust'] is True
    assert widget.values()['browser_selection_frame_rounded'] is False
    widget.deleteLater(); qapp.processEvents()


def test_individual_selection_defaults_reset_only_their_own_value(qapp):
    widget = BrowserWorkflowSettings()
    widget.load({
        "browser_selection_filename_opacity": 73,
        "browser_selection_border_width": 9,
        "browser_selection_text_color_auto_adjust": False,
        "browser_selection_frame_rounded": True,
    })

    widget.opacity_reset_button.click()
    assert widget.opacity.value() == 38
    assert widget.border_width_spin.value() == 9
    assert not widget.auto_adjust_text_color.isChecked()
    assert widget.rounded_selection_frame.isChecked()

    widget.border_width_reset_button.click()
    assert widget.opacity.value() == 38
    assert widget.border_width_spin.value() == 2
    widget.deleteLater()
    qapp.processEvents()


def test_browser_workflow_preferences_persist_through_config_reload(tmp_path):
    path = tmp_path / "settings.json"
    config = ConfigManager(path)
    config.load()
    assert config.get("browser_selection_text_color_auto_adjust") is True
    assert config.get("browser_selection_frame_rounded") is False
    config.apply({
        "browser_selection_text_color_auto_adjust": False,
        "browser_selection_frame_rounded": True,
    }, save=True)

    reloaded = ConfigManager(path)
    reloaded.load()
    assert reloaded.get("browser_selection_text_color_auto_adjust") is False
    assert reloaded.get("browser_selection_frame_rounded") is True


@pytest.mark.parametrize(
    "base,highlight,text_color,selection_color",
    [
        ("#ffffff", "#ffff00", "#25364a", "#ffff00"),
        ("#202020", "#ffffff", "#e2e8f0", "#ffffff"),
        ("#303030", "#111111", "#c8d0dd", "#111111"),
    ],
)
def test_selected_filename_text_auto_adjust_can_be_disabled(
    qapp, base, highlight, text_color, selection_color
):
    delegate = BrowserItemDelegate()
    option = QStyleOptionViewItem()
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Base, QColor(base))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(highlight))
    palette.setColor(QPalette.ColorRole.Text, QColor(text_color))
    option.palette = palette

    delegate.selection_appearance = SelectionAppearance.from_settings({
        "browser_selection_color": selection_color,
        "browser_selection_text_color_auto_adjust": False,
    })
    assert delegate._selection_text_color(option) == QColor(text_color)

    for selection_base, selection_color, expected in (
        ("#ffffff", "#ffffff", "#000000"),
        ("#101010", "#101010", "#ffffff"),
    ):
        palette.setColor(
            QPalette.ColorRole.Base, QColor(selection_base)
        )
        option.palette = palette
        delegate.selection_appearance = SelectionAppearance.from_settings({
            "browser_selection_color": selection_color,
            "browser_selection_text_color_auto_adjust": True,
        })
        assert delegate._selection_text_color(option) == QColor(expected)


def test_selection_appearance_preferences_apply_immediately(tmp_path, qapp):
    path = tmp_path / "image.jpg"
    item = BrowserItem(path.name, path, BrowserItemKind.IMAGE, 1.0)
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    generation = provider.begin_generation()
    window = _WorkflowWindow()
    window._shutdown_prepared = False
    window._generation = generation
    window._fast_scrolling = False
    window._thumbnail_scroll_direction = 1
    window._first_paint_pending_generation = None
    window._thumbnail_request_timer = None
    window._visible_row_range = lambda: (0, 0)
    window._schedule_thumbnail_requests = lambda _delay: None
    window.thumbnail_provider = provider
    window.thumbnail_render_spec = ThumbnailRenderSpec.from_settings(
        64, "square_1_1", "letterbox"
    )
    window.item_model = _WorkflowModel([item])
    window.config = _WorkflowConfig()
    window.browser_search_edit = QLineEdit(window)
    window.item_delegate = SimpleNamespace(selection_appearance=None)
    window.list_view = _WorkflowListView(window)
    workflow = BrowserWorkflowController(window)

    window.config.data.update({
        "browser_selection_color": "#f0f000",
        "browser_selection_text_color_auto_adjust": False,
        "browser_selection_frame_rounded": True,
    })
    window.config.settings_changed.emit({
        "browser_selection_text_color_auto_adjust": False,
        "browser_selection_frame_rounded": True,
    })

    assert workflow.options["browser_selection_text_color_auto_adjust"] is False
    assert workflow.options["browser_selection_frame_rounded"] is True
    assert workflow.window.item_delegate.selection_appearance.auto_adjust_text_color is False
    assert workflow.window.item_delegate.selection_appearance.rounded_frame is True
    provider.close()
    window.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("dpr", [1, 2])
def test_selection_frame_uses_square_miter_corners_by_default(qapp, dpr):
    delegate = BrowserItemDelegate()
    option = QStyleOptionViewItem()
    option.state = QStyle.StateFlag.State_Selected
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#ff0000"))
    option.palette = palette

    def render(rounded):
        image = QImage(
            100 * dpr,
            100 * dpr,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        delegate.selection_appearance = SelectionAppearance.from_settings({
            "browser_selection_color": "#ff0000",
            "browser_selection_border_width": 4,
            "browser_selection_frame_rounded": rounded,
        })
        delegate._paint_interaction_frame(
            painter, option, QRect(20, 20, 60, 60)
        )
        painter.end()
        return image

    square = render(False)
    rounded = render(True)
    corner = 20 * dpr
    top = (50 * dpr, 20 * dpr)
    assert square.pixelColor(corner, corner).red() > 0
    assert rounded.pixelColor(corner, corner).alpha() == 0
    assert square.pixelColor(*top).red() > 0
    assert rounded.pixelColor(*top).red() > 0


class _WorkflowModel(QObject):
    modelReset = Signal()
    layoutChanged = Signal()
    rowsInserted = Signal(object, int, int)
    rowsRemoved = Signal(object, int, int)
    dataChanged = Signal(object, object, object)
    ItemRole = 259

    def __init__(self, items):
        super().__init__()
        self.items = items

    def rowCount(self):
        return len(self.items)

    def item_at(self, row):
        return self.items[row] if 0 <= row < len(self.items) else None


def test_same_path_revision_change_invalidates_background_completion(tmp_path, qapp):
    visible_path = tmp_path / "visible.jpg"
    background_path = tmp_path / "mutable.jpg"
    visible_path.write_bytes(b"visible")
    background_path.write_bytes(b"first revision")
    visible = BrowserItem(
        visible_path.name,
        visible_path,
        BrowserItemKind.IMAGE,
        1.0,
        file_size=7,
        modified_time_ns=1,
    )
    first_revision = BrowserItem(
        background_path.name,
        background_path,
        BrowserItemKind.IMAGE,
        1.0,
        file_size=14,
        modified_time_ns=1,
    )

    def loader(_item, _size, _cancel_token):
        image = QImage(16, 16, QImage.Format.Format_RGBA8888)
        image.fill(0xFF336699)
        return image

    provider = BrowserThumbnailProvider(
        loader=loader,
        disk_cache_enabled=False,
    )
    generation = provider.begin_generation()
    window = _WorkflowWindow()
    window._shutdown_prepared = False
    window._generation = generation
    window._fast_scrolling = False
    window._thumbnail_scroll_direction = 1
    window._first_paint_pending_generation = None
    window._thumbnail_request_timer = None
    window._visible_row_range = lambda: (0, 0)
    window._schedule_thumbnail_requests = lambda _delay: None
    window.thumbnail_provider = provider
    spec = ThumbnailRenderSpec.from_settings(64, "square_1_1", "letterbox")
    window.thumbnail_render_spec = spec
    model = _WorkflowModel([visible, first_revision])
    window.item_model = model
    window.config = _WorkflowConfig()
    window.browser_search_edit = QLineEdit(window)
    window.item_delegate = SimpleNamespace(selection_appearance=None)
    window.list_view = _WorkflowListView(window)
    workflow = BrowserWorkflowController(window)

    def wait_for_background_count(expected):
        import time

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            qapp.processEvents()
            provider.wait_for_done(20)
            qapp.processEvents()
            if provider.cache_statistics().get("generated_background", 0) >= expected:
                return
            time.sleep(0.005)
        pytest.fail(f"background generation did not reach {expected}")

    workflow._pump()
    wait_for_background_count(1)
    old_key = (
        provider._path_key(background_path),
        spec.cache_token,
        first_revision.thumbnail_revision,
    )
    assert old_key in provider._cache

    second_revision = BrowserItem(
        background_path.name,
        background_path,
        BrowserItemKind.IMAGE,
        2.0,
        file_size=28,
        modified_time_ns=2,
    )
    model.items[1] = second_revision
    changed_index = SimpleNamespace(row=lambda: 1)
    model.dataChanged.emit(changed_index, changed_index, [model.ItemRole])
    wait_for_background_count(2)

    new_key = (
        provider._path_key(background_path),
        spec.cache_token,
        second_revision.thumbnail_revision,
    )
    assert new_key in provider._cache
    assert provider.cache_statistics()["generated_background"] == 2
    provider.close()
    window.deleteLater()
    qapp.processEvents()


class _WorkflowConfig(QObject):
    settings_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.data = {"browser_thumbnail_background_screens": 1}


class _WorkflowListView:
    def __init__(self, parent):
        self._viewport = QWidget(parent)

    def viewport(self):
        return self._viewport


class _WorkflowWindow(QWidget):
    def isVisible(self):
        return True


def test_metadata_completion_resumes_real_browser_background_generation(tmp_path, qapp):
    visible_path = tmp_path / "visible.jpg"
    with Image.new("RGB", (24, 32), "white") as source:
        source.save(visible_path)
    folder = tmp_path / "next-folder"
    folder.mkdir()
    with Image.new("RGB", (48, 24), "blue") as source:
        source.save(folder / "01.jpg")
    visible = BrowserItem(
        visible_path.name, visible_path, BrowserItemKind.IMAGE,
        visible_path.stat().st_mtime,
    )
    background = BrowserItem(
        folder.name, folder, BrowserItemKind.FOLDER, folder.stat().st_mtime,
    )
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    generation = provider.begin_generation()
    window = _WorkflowWindow()
    window._shutdown_prepared = False
    window._generation = generation
    window._fast_scrolling = False
    window._thumbnail_scroll_direction = 1
    window._first_paint_pending_generation = None
    window._thumbnail_request_timer = None
    window._visible_row_range = lambda: (0, 0)
    window._schedule_thumbnail_requests = lambda _delay: None
    window.thumbnail_provider = provider
    window.thumbnail_render_spec = ThumbnailRenderSpec.from_settings(
        96, "square_1_1", "letterbox"
    )
    window.item_model = _WorkflowModel([visible, background])
    window.config = _WorkflowConfig()
    window.browser_search_edit = QLineEdit(window)
    window.item_delegate = SimpleNamespace(selection_appearance=None)
    window.list_view = _WorkflowListView(window)
    workflow = BrowserWorkflowController(window)

    assert provider.request_page_count(background, generation=generation)
    workflow._pump()
    assert provider.cache_statistics().get("generated_background", 0) == 0

    import time

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        qapp.processEvents()
        provider.wait_for_done(20)
        qapp.processEvents()
        if provider.cache_statistics().get("generated_background", 0) == 1:
            break
        time.sleep(0.005)

    assert provider.cache_statistics().get("generated_background", 0) == 1
    assert provider.pending_count == 0
    provider.close()
    window.deleteLater()
    qapp.processEvents()


def test_three_forward_screens_generate_120_real_file_thumbnails(tmp_path, qapp):
    items = []
    for row in range(160):
        path = tmp_path / f"page-{row:03}.jpg"
        with Image.new("RGB", (24, 32), (row % 255, 100, 180)) as source:
            source.save(path)
        items.append(BrowserItem(
            path.name, path, BrowserItemKind.IMAGE, path.stat().st_mtime,
        ))

    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    generation = provider.begin_generation()
    window = _WorkflowWindow()
    window._shutdown_prepared = False
    window._generation = generation
    window._fast_scrolling = False
    window._thumbnail_scroll_direction = 1
    window._first_paint_pending_generation = None
    window._thumbnail_request_timer = None
    window._visible_row_range = lambda: (0, 39)
    window._schedule_thumbnail_requests = lambda _delay: None
    window.thumbnail_provider = provider
    window.thumbnail_render_spec = ThumbnailRenderSpec.from_settings(
        64, "square_1_1", "letterbox"
    )
    window.item_model = _WorkflowModel(items)
    window.config = _WorkflowConfig()
    window.config.data["browser_thumbnail_background_screens"] = 3
    window.browser_search_edit = QLineEdit(window)
    window.item_delegate = SimpleNamespace(selection_appearance=None)
    window.list_view = _WorkflowListView(window)
    workflow = BrowserWorkflowController(window)

    workflow._pump()
    import time

    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        qapp.processEvents()
        provider.wait_for_done(20)
        qapp.processEvents()
        if provider.cache_statistics().get("generated_background", 0) >= 120:
            break
        time.sleep(0.003)

    stats = provider.cache_statistics()
    assert stats.get("generated_background", 0) == 120
    cached_paths = {entry[0] for entry in provider._cache}
    expected_paths = {
        provider._path_key(item.path) for item in items[40:160]
    }
    assert cached_paths == expected_paths
    assert stats["memory_cache_entries"] == 120
    assert stats["memory_cache_usage_bytes"] <= stats["memory_cache_capacity_bytes"]
    assert provider.pending_count == 0

    provider.close()
    window.deleteLater()
    qapp.processEvents()


def test_failed_background_thumbnail_does_not_block_later_rows(tmp_path, qapp):
    visible_path = tmp_path / "visible.jpg"
    failed_path = tmp_path / "broken.jpg"
    ready_path = tmp_path / "ready.jpg"
    for path, color in ((visible_path, "white"), (ready_path, "blue")):
        with Image.new("RGB", (24, 32), color) as source:
            source.save(path)
    failed_path.write_bytes(b"not an image")
    items = [
        BrowserItem(path.name, path, kind, path.stat().st_mtime)
        for path, kind in (
            (visible_path, BrowserItemKind.IMAGE),
            (failed_path, BrowserItemKind.IMAGE),
            (ready_path, BrowserItemKind.IMAGE),
        )
    ]
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    generation = provider.begin_generation()
    window = _WorkflowWindow()
    window._shutdown_prepared = False
    window._generation = generation
    window._fast_scrolling = False
    window._thumbnail_scroll_direction = 1
    window._first_paint_pending_generation = None
    window._thumbnail_request_timer = None
    window._visible_row_range = lambda: (0, 0)
    window._schedule_thumbnail_requests = lambda _delay: None
    window.thumbnail_provider = provider
    window.thumbnail_render_spec = ThumbnailRenderSpec.from_settings(
        64, "square_1_1", "letterbox"
    )
    window.item_model = _WorkflowModel(items)
    window.config = _WorkflowConfig()
    window.config.data["browser_thumbnail_background_screens"] = 2
    window.browser_search_edit = QLineEdit(window)
    window.item_delegate = SimpleNamespace(selection_appearance=None)
    window.list_view = _WorkflowListView(window)
    workflow = BrowserWorkflowController(window)

    workflow._pump()
    import time

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        qapp.processEvents()
        provider.wait_for_done(20)
        qapp.processEvents()
        if provider.cache_statistics().get("generated_background", 0) == 1:
            break
        time.sleep(0.005)

    stats = provider.cache_statistics()
    assert provider.has_failed_requests
    assert stats.get("generated_background", 0) == 1
    assert provider._path_key(ready_path) in {entry[0] for entry in provider._cache}
    assert provider.pending_count == 0
    provider.close()
    window.deleteLater()
    qapp.processEvents()


def test_real_move_undo_stays_on_file_operation_service(tmp_path):
    source=tmp_path/'a'/'file.txt'; source.parent.mkdir()
    dest=tmp_path/'b'; dest.mkdir(); source.write_text('data',encoding='utf-8')
    service=FileOperationService()
    result=service.execute(FileOperationRequest(1,FileOperationKind.MOVE,(str(source),),str(dest)))
    assert result.successes and result.undo_entries
    undone=service.execute(FileOperationRequest(2,FileOperationKind.UNDO,
                            undo_entries=result.undo_entries))
    assert undone.successes and source.read_text(encoding='utf-8')=='data'
    assert not (dest/source.name).exists()
    assert not undone.undo_entries


def test_shortcut_migration_preserves_explicit_unassigned(qapp):
    migrated=normalize_shortcut_bindings({'browser':{'browser_copy':['Alt+C']}})['browser']
    assert migrated['browser_focus_search']==['Ctrl+F'] and migrated['browser_undo']==['Ctrl+Z']
    explicit=normalize_shortcut_bindings({'browser':{'browser_undo':[]}})['browser']
    assert explicit['browser_undo']==[]
    custom=normalize_shortcut_bindings({'browser':{'browser_copy':['Ctrl+F'],
                                                  'browser_cut':['Ctrl+Z']}})['browser']
    assert custom['browser_focus_search']==[] and custom['browser_undo']==[]
    assert custom['browser_copy']==['Ctrl+F'] and custom['browser_cut']==['Ctrl+Z']
