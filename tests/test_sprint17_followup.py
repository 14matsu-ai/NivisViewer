from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import (
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    QPointF,
    QSize,
    Qt,
)
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFocusEvent,
    QKeyEvent,
    QMouseEvent,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QListView

from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_pointer_controller import (
    BrowserPointerController,
    BrowserPointerState,
)
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.drag_drop import build_path_mime_data
from app.explorer_list_view import ExplorerListView, PathDropTreeView
from app.file_operation_service import FileOperationKind
from app.favorite_item_delegate import FavoriteItemDelegate
from app.favorite_row_metrics import FavoriteRowMetrics
from app.folder_tree_pointer import (
    FolderTreePointerController,
    FolderTreePointerState,
)
from app.image_source import (
    FolderImageSource,
    FolderListingSnapshot,
    ImageSource,
    create_image_source,
)
from app.settings_dialog import SettingsDialog
from app.sidebar_history_view import SidebarHistoryView
from app.metadata_store import MetadataStore
from app.page_model import PageModel
from app.viewer_window import ViewerWindow


def _browser_item(path: Path, kind: BrowserItemKind = BrowserItemKind.IMAGE):
    return BrowserItem(path.name, path.absolute(), kind, None)


def _make_list_view(qapp, tmp_path: Path, count: int = 6):
    model = BrowserItemModel()
    paths = [tmp_path / f"{index:02d}.jpg" for index in range(count)]
    model.set_items([_browser_item(path) for path in paths])
    view = ExplorerListView()
    view.setModel(model)
    view.setViewMode(QListView.ViewMode.IconMode)
    view.setGridSize(QSize(80, 80))
    view.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
    view.resize(360, 260)
    view.show()
    qapp.processEvents()
    return view, model, paths


def _center(view: ExplorerListView, model: BrowserItemModel, row: int) -> QPoint:
    return view.visualRect(model.index(row, 0)).center()


def _blank(view: ExplorerListView) -> QPoint:
    point = QPoint(view.viewport().width() - 4, view.viewport().height() - 4)
    assert not view.indexAt(point).isValid()
    return point


def _left_move(view, position: QPoint, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(position),
        QPointF(view.viewport().mapToGlobal(position)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        modifiers,
    )
    return event


def test_pointer_controller_has_one_state_and_captures_press_snapshot() -> None:
    controller = BrowserPointerController()
    controller.begin(
        position=QPoint(2, 3),
        global_position=QPoint(12, 13),
        modifiers=Qt.KeyboardModifier.ControlModifier,
        row=4,
        path=r"C:\本\04.webp",
        was_selected=True,
        selection_snapshot=(r"C:\本\03.webp", r"C:\本\04.webp"),
        current_path=r"C:\本\03.webp",
        anchor_path=r"C:\本\01.webp",
    )
    assert controller.state is BrowserPointerState.PRESSED_ON_ITEM
    assert controller.press is not None
    assert controller.press.selection_snapshot[-1].endswith("04.webp")
    assert not controller.begin_file_drag()
    controller.cancel()
    assert controller.state is BrowserPointerState.CANCELLED
    controller.reset()
    assert controller.state is BrowserPointerState.IDLE


def test_plain_ctrl_and_shift_click_selection(qapp, tmp_path: Path) -> None:
    view, model, _paths = _make_list_view(qapp, tmp_path)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=_center(view, model, 1))
    assert [index.row() for index in view.selectedIndexes()] == [1]

    QTest.mouseClick(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
        _center(view, model, 3),
    )
    assert sorted(index.row() for index in view.selectedIndexes()) == [1, 3]

    QTest.mouseClick(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        _center(view, model, 5),
    )
    assert sorted(index.row() for index in view.selectedIndexes()) == [3, 4, 5]
    view.close()


def test_selected_multi_drag_uses_snapshot_once_and_release_does_not_click(
    qapp,
    tmp_path: Path,
) -> None:
    view, model, paths = _make_list_view(qapp, tmp_path)
    selection = view.selectionModel()
    for row in (1, 2, 4):
        selection.select(
            model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select,
        )
    calls: list[tuple[str, ...]] = []
    clicks: list[int] = []
    view._execute_path_drag = lambda drag_paths: calls.append(drag_paths) or True
    view.clicked.connect(lambda index: clicks.append(index.row()))
    start = _center(view, model, 2)
    end = start + QPoint(30, 25)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qapp.sendEvent(view.viewport(), _left_move(view, end))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert calls == [
        tuple(str(paths[row].absolute()) for row in (1, 2, 4))
    ]
    assert clicks == []
    assert sorted(index.row() for index in view.selectedIndexes()) == [1, 2, 4]
    assert view.pointer_controller.state is BrowserPointerState.IDLE
    view.close()


def test_unselected_drag_selects_and_drags_only_pressed_path(
    qapp,
    tmp_path: Path,
) -> None:
    view, model, paths = _make_list_view(qapp, tmp_path)
    view.selectionModel().select(
        model.index(0, 0),
        QItemSelectionModel.SelectionFlag.Select,
    )
    calls: list[tuple[str, ...]] = []
    view._execute_path_drag = lambda drag_paths: calls.append(drag_paths) or True
    start = _center(view, model, 3)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qapp.sendEvent(view.viewport(), _left_move(view, start + QPoint(30, 20)))
    QTest.mouseRelease(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        pos=start + QPoint(30, 20),
    )
    assert calls == [(str(paths[3].absolute()),)]
    assert [index.row() for index in view.selectedIndexes()] == [3]
    view.close()


def test_blank_click_clears_plain_drag_preserves_and_shift_drag_selects(
    qapp,
    tmp_path: Path,
) -> None:
    view, model, _paths = _make_list_view(qapp, tmp_path)
    view.selectionModel().select(
        model.index(0, 0),
        QItemSelectionModel.SelectionFlag.Select,
    )
    blank = _blank(view)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=blank)
    assert not view.selectedIndexes()

    view.selectionModel().select(
        model.index(1, 0),
        QItemSelectionModel.SelectionFlag.Select,
    )
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=blank)
    qapp.sendEvent(view.viewport(), _left_move(view, _center(view, model, 4)))
    QTest.mouseRelease(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        pos=_center(view, model, 4),
    )
    assert [index.row() for index in view.selectedIndexes()] == [1]

    QTest.mousePress(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        blank,
    )
    qapp.sendEvent(
        view.viewport(),
        _left_move(
            view,
            _center(view, model, 0),
            Qt.KeyboardModifier.ShiftModifier,
        ),
    )
    QTest.mouseRelease(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        _center(view, model, 0),
    )
    assert len(view.selectedIndexes()) >= 2
    assert not view._rubber_band.isVisible()
    view.close()


def test_drag_re_resolves_paths_after_sort_and_drops_removed_items(
    qapp,
    tmp_path: Path,
) -> None:
    view, model, paths = _make_list_view(qapp, tmp_path, 4)
    selection = view.selectionModel()
    for row in (1, 2):
        selection.select(model.index(row, 0), QItemSelectionModel.SelectionFlag.Select)
    calls: list[tuple[str, ...]] = []
    view._execute_path_drag = lambda drag_paths: calls.append(drag_paths) or True
    start = _center(view, model, 1)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    model.set_items([_browser_item(paths[row]) for row in (3, 1, 0)])
    current = model.index(model.row_for_path(paths[1]), 0)
    move_to = view.visualRect(current).center() + QPoint(30, 20)
    qapp.sendEvent(view.viewport(), _left_move(view, move_to))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=move_to)
    assert calls == [(str(paths[1].absolute()),)]
    view.close()


def test_drag_cancel_focus_out_and_double_click_open_once(
    qapp,
    tmp_path: Path,
) -> None:
    view, model, _paths = _make_list_view(qapp, tmp_path)
    opens: list[int] = []
    view.activated.connect(lambda index: opens.append(index.row()))
    point = _center(view, model, 0)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    qapp.sendEvent(view, QFocusEvent(QEvent.Type.FocusOut))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert opens == []
    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert opens == [0]
    view.close()


class _LazyIdentitySource(ImageSource):
    load_sizes_lazily = True

    def __init__(self, ids: list[str]) -> None:
        super().__init__("book")
        self.ids = ids

    def list_images(self) -> list[str]:
        return list(self.ids)

    def open_image(self, image_id: str):
        raise AssertionError(f"GUI test must not decode {image_id}")

    def display_path(self, image_id: str) -> str:
        return image_id


@pytest.mark.parametrize("wide_index", [2, 6, 10])
@pytest.mark.parametrize("single_first", [False, True])
@pytest.mark.parametrize("direction", ["ltr", "rtl"])
def test_delayed_wide_size_keeps_requested_identity_and_single_unit(
    wide_index: int,
    single_first: bool,
    direction: str,
) -> None:
    ids = [f"{index + 1:02d}_{'wide' if index in {2, 6, 10} else 'portrait'}.webp" for index in range(12)]
    source = _LazyIdentitySource(ids)
    model = PageModel()
    model.update_options(
        view_mode="spread",
        single_first_page=single_first,
        reading_direction=direction,
        treat_wide_image_as_single=True,
    )
    selected = ids[wide_index]
    model.set_prepared_source(source, ids, selected)
    assert model.focused_index == wide_index
    assert selected in [slot.image_id for slot in model.spread_at().slots]
    for index in range(12):
        model.set_image_size(
            index,
            (1600, 800) if index in {2, 6, 10} else (800, 1200),
        )
    spread = model.spread_at()
    assert model.focused_index == wide_index
    assert [slot.page_index for slot in spread.slots] == [wide_index]
    assert model.display_path_for_index(model.focused_index) == selected


def test_folder_source_identity_uses_normalized_absolute_paths(tmp_path: Path) -> None:
    images = tuple(str((tmp_path / name).absolute()) for name in ("1.jpg", "2.jpg", "10.jpg"))
    # Snapshots retain only existing local files. This test concerns path
    # identity, so provide those files without involving image decoding.
    for image in images:
        Path(image).touch()
    source = FolderImageSource(tmp_path, image_snapshot=images)
    assert source.index_for_path(images[1]) == 1
    assert source.path_for_index(2) == images[2]
    assert source.index_for_identity(source.page_identity(images[0])) == 0


def test_browser_snapshot_contains_only_images_and_explicit_identity(
    qapp,
    tmp_path: Path,
) -> None:
    folder = tmp_path / "本"
    folder.mkdir()
    selected = folder / "03_wide.webp"
    items = [
        _browser_item(folder / "subfolder", BrowserItemKind.FOLDER),
        _browser_item(folder / "notes.txt", BrowserItemKind.OTHER),
        _browser_item(folder / "01_portrait.webp"),
        _browser_item(selected),
        _browser_item(folder / "book.cbz", BrowserItemKind.ARCHIVE),
        _browser_item(folder / "07_wide.webp"),
    ]
    window = BrowserWindow(
        config_manager=ConfigManager(tmp_path / "config.json"),
        restore_initial_location=False,
    )
    window.current_path = folder
    window.item_model.set_items(items)
    item = window.item_model.item_at(window.item_model.row_for_path(selected))
    assert item is not None
    snapshot = window._folder_snapshot_for_item(item)
    assert snapshot is not None
    assert snapshot.selected_image == str(selected)
    assert snapshot.generation == window._scan_generation
    assert snapshot.sort_identity.startswith("name:")
    assert all(Path(path).suffix == ".webp" for path in snapshot.image_ids)
    assert snapshot.selected_image in snapshot.image_ids
    window.close()
    qapp.processEvents()


def test_stale_folder_snapshot_falls_back_to_source_listing(tmp_path: Path) -> None:
    first = tmp_path / "1.jpg"
    selected = tmp_path / "2.jpg"
    Image.new("RGB", (10, 20), "white").save(first)
    Image.new("RGB", (10, 20), "white").save(selected)
    stale = FolderListingSnapshot(
        tmp_path,
        (str(first),),
        str(first),
        generation=1,
        sort_identity="name:ascending",
    )
    source, selected_image = create_image_source(selected, folder_snapshot=stale)
    assert selected_image == str(selected)
    assert source.list_images() == [str(first), str(selected)]
    source.close()


class _TreePathModel(QStandardItemModel):
    PathRole = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self) -> None:
        super().__init__()
        self.by_path: dict[str, QStandardItem] = {}

    def add_path(self, path: str, parent: QStandardItem | None = None) -> QStandardItem:
        item = QStandardItem(Path(path).name or path)
        item.setData(path, self.PathRole)
        (parent or self.invisibleRootItem()).appendRow(item)
        self.by_path[path] = item
        return item

    def filePath(self, index: QModelIndex) -> str:  # noqa: N802
        return str(index.data(self.PathRole) or "")

    def index(self, *args):  # type: ignore[override]
        if len(args) == 1 and isinstance(args[0], str):
            item = self.by_path.get(args[0])
            return item.index() if item is not None else QModelIndex()
        return super().index(*args)


def _make_tree(qapp):
    model = _TreePathModel()
    first = model.add_path(r"C:\A")
    child = model.add_path(r"C:\A\Child", first)
    model.add_path(r"C:\A\Child\Grandchild", child)
    model.add_path(r"C:\B")
    tree = PathDropTreeView()
    tree.setModel(model)
    tree.expandAll()
    tree.resize(320, 260)
    tree.show()
    qapp.processEvents()
    return tree, model


def test_tree_controller_only_confirms_same_row_click() -> None:
    controller = FolderTreePointerController()
    assert controller.begin("A", QPoint(1, 1), disclosure=False)
    assert controller.confirm_release("B", QPoint(1, 2), 10) is None
    controller.begin("A", QPoint(1, 1), disclosure=False)
    assert controller.confirm_release("A", QPoint(2, 2), 10) == "A"
    controller.begin_programmatic_sync()
    assert controller.state is FolderTreePointerState.PROGRAMMATIC_SYNC
    assert not controller.begin("A", QPoint(), disclosure=False)


def test_tree_real_click_drag_hover_enter_and_programmatic_sync(qapp) -> None:
    tree, model = _make_tree(qapp)
    calls: list[str] = []
    tree.navigationConfirmed.connect(lambda index: calls.append(model.filePath(index)))
    first = model.index(r"C:\A")
    second = model.index(r"C:\B")
    first_pos = tree.visualRect(first).center()
    second_pos = tree.visualRect(second).center()
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=first_pos)
    assert calls == [r"C:\A"]

    QTest.mousePress(tree.viewport(), Qt.MouseButton.LeftButton, pos=first_pos)
    qapp.sendEvent(tree.viewport(), _left_move(tree, second_pos))
    QTest.mouseRelease(tree.viewport(), Qt.MouseButton.LeftButton, pos=second_pos)
    assert calls == [r"C:\A"]

    tree.setCurrentIndex(second)
    qapp.sendEvent(
        tree,
        QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    assert calls == [r"C:\A", r"C:\B"]

    tree.set_programmatic_sync(True)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=first_pos)
    tree.set_programmatic_sync(False)
    assert calls == [r"C:\A", r"C:\B"]
    tree.close()


def test_tree_disclosure_only_toggles_and_drop_hover_only_highlights(qapp, tmp_path: Path) -> None:
    tree, model = _make_tree(qapp)
    calls: list[str] = []
    drops: list[tuple[tuple[str, ...], str]] = []
    tree.navigationConfirmed.connect(lambda index: calls.append(model.filePath(index)))
    tree.paths_dropped.connect(
        lambda paths, index, _mods, _source: drops.append(
            (paths, model.filePath(index))
        )
    )
    child = model.index(r"C:\A\Child")
    rectangle = tree.visualRect(child)
    disclosure = QPoint(max(0, rectangle.left() - 3), rectangle.center().y())
    assert tree._is_disclosure_position(child, disclosure)
    was_expanded = tree.isExpanded(child)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=disclosure)
    assert tree.isExpanded(child) is not was_expanded
    assert calls == []

    source = str((tmp_path / "source.cbz").absolute())
    mime = build_path_mime_data((source,))
    target = model.index(r"C:\B")
    position = tree.visualRect(target).center()
    enter = QDragEnterEvent(
        position,
        Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    qapp.sendEvent(tree.viewport(), enter)
    move = QDragMoveEvent(
        position,
        Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    qapp.sendEvent(tree.viewport(), move)
    assert tree._drop_hover_index == target
    assert calls == []
    drop = QDropEvent(
        QPointF(position),
        Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
    )
    qapp.sendEvent(tree.viewport(), drop)
    assert drops == [((source,), r"C:\B")]
    assert calls == []
    tree.close()


@pytest.mark.parametrize("padding", [0, 1, 8])
@pytest.mark.parametrize("spacing", [0, 8])
@pytest.mark.parametrize("icon_size", [14, 16, 24])
def test_favorite_row_height_is_exact_formula(
    padding: int,
    spacing: int,
    icon_size: int,
) -> None:
    metrics = FavoriteRowMetrics.normalized(
        padding_y=padding,
        spacing=spacing,
        icon_size=icon_size,
    )
    assert metrics.row_height(12) == max(12, icon_size) + padding * 2
    assert metrics.spacing == spacing


def test_favorite_delegate_height_ignores_long_unc_missing_text(qapp) -> None:
    view = QListView()
    model = QStandardItemModel()
    for text in (
        "短い名前",
        "非常に長い日本語のお気に入りフォルダ名" * 8,
        r"\\server\share\日本語\深い\フォルダ — 見つかりません",
    ):
        model.appendRow(QStandardItem(text))
    metrics = FavoriteRowMetrics(padding_y=1, spacing=0, icon_size=16)
    view.setModel(model)
    view.setItemDelegate(FavoriteItemDelegate(view, metrics=metrics))
    view.setUniformItemSizes(True)
    view.show()
    qapp.processEvents()
    expected_height = metrics.row_height(view.fontMetrics().height())
    assert [view.sizeHintForRow(row) for row in range(3)] == [expected_height] * 3
    view.close()


def test_favorite_config_round_trip_and_normalization(tmp_path: Path) -> None:
    config = ConfigManager(tmp_path / "config.json")
    defaults = config.load()
    assert defaults["favorite_row_padding_y"] == 1
    assert defaults["favorite_row_spacing"] == 0
    assert defaults["favorite_icon_size"] == 16
    assert defaults["favorite_drop_default_operation"] == "copy"
    assert defaults["favorite_drop_ctrl_inverts_operation"] is True
    assert defaults["favorite_drop_confirm_move"] is True
    assert defaults["favorite_drop_confirm_focus_yes"] is False
    assert defaults["sidebar_drop_folders_to_favorites"] is False
    config.apply(
        {
            "favorite_row_padding_y": 99,
            "favorite_row_spacing": -1,
            "favorite_icon_size": 99,
            "favorite_drop_default_operation": "invalid",
            "favorite_drop_ctrl_inverts_operation": "invalid",
        },
        save=True,
    )
    reopened = ConfigManager(tmp_path / "config.json")
    reopened.load()
    assert reopened.get("favorite_row_padding_y") == 8
    assert reopened.get("favorite_row_spacing") == 0
    assert reopened.get("favorite_icon_size") == 24
    assert reopened.get("favorite_drop_default_operation") == "copy"
    assert reopened.get("favorite_drop_ctrl_inverts_operation") is True


def test_favorite_settings_round_trip_and_existing_window_apply(
    qapp,
    tmp_path: Path,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = BrowserWindow(
        config_manager=config,
        restore_initial_location=False,
    )
    dialog = SettingsDialog(config)
    dialog.favorite_row_padding_spin.setValue(0)
    dialog.favorite_row_spacing_spin.setValue(3)
    dialog.favorite_icon_size_spin.setValue(24)
    dialog.favorite_drop_default_operation_combo.setCurrentIndex(
        dialog.favorite_drop_default_operation_combo.findData("move")
    )
    dialog.favorite_drop_ctrl_inverts_checkbox.setChecked(False)
    dialog.favorite_drop_confirm_move_checkbox.setChecked(False)
    dialog.favorite_drop_confirm_focus_yes_checkbox.setChecked(True)
    dialog.sidebar_drop_folders_to_favorites_checkbox.setChecked(True)
    changed = dialog.apply_settings()
    assert changed["favorite_row_padding_y"] == 0
    assert changed["favorite_row_spacing"] == 3
    assert changed["favorite_icon_size"] == 24
    assert changed["favorite_drop_default_operation"] == "move"
    assert changed["favorite_drop_ctrl_inverts_operation"] is False
    assert changed["favorite_drop_confirm_move"] is False
    assert changed["favorite_drop_confirm_focus_yes"] is True
    assert changed["sidebar_drop_folders_to_favorites"] is True
    assert window.favorite_row_metrics == FavoriteRowMetrics(0, 3, 24)
    assert window.favorite_view.spacing() == 3
    assert window.favorite_view.iconSize() == QSize(24, 24)
    assert window.favorite_drop_default_operation == "move"
    assert window.favorite_drop_ctrl_inverts_operation is False
    assert window.favorite_drop_confirm_move is False
    assert window.favorite_drop_confirm_focus_yes is True
    assert window.sidebar_drop_folders_to_favorites is True
    assert window.history_view.acceptDrops()
    dialog.reject()
    window.close()
    qapp.processEvents()


def test_favorite_drop_default_copy_and_ctrl_inversion(
    qapp,
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    source = tmp_path / "source.txt"
    source.write_text("x", encoding="utf-8")
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.add_folder_bookmark(str(target))
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"favorite_drop_confirm_move": False})
    window = BrowserWindow(
        config_manager=config,
        metadata_store=store,
        restore_initial_location=False,
    )
    calls: list[tuple[FileOperationKind, dict[str, object]]] = []
    window._start_file_operation = (
        lambda operation, **kwargs: calls.append((operation, kwargs)) or True
    )
    index = window.folder_bookmark_model.index(0, 0)

    window._on_favorite_paths_dropped(
        (str(source),),
        index,
        Qt.KeyboardModifier.NoModifier,
        window.list_view,
    )
    assert calls[-1][0] is FileOperationKind.COPY

    window._on_favorite_paths_dropped(
        (str(source),),
        index,
        Qt.KeyboardModifier.ControlModifier,
        window.list_view,
    )
    assert calls[-1][0] is FileOperationKind.MOVE

    window.close()
    qapp.processEvents()
    store.close()


def test_favorite_actual_release_navigation_and_double_click_once(
    qapp,
    tmp_path: Path,
) -> None:
    folder = tmp_path / "お気に入り"
    folder.mkdir()
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.add_folder_bookmark(str(folder))
    window = BrowserWindow(
        config_manager=ConfigManager(tmp_path / "config.json"),
        metadata_store=store,
        restore_initial_location=False,
    )
    window.show()
    qapp.processEvents()
    window.activateWindow()
    window.favorite_view.setFocus()
    qapp.processEvents()
    calls: list[str] = []
    window.navigate_to = lambda path, **_kwargs: calls.append(str(path)) or True
    window._favorite_click_timer.setInterval(1)
    index = window.folder_bookmark_model.index(0, 0)
    point = window.favorite_view.visualRect(index).center()
    QTest.mouseClick(
        window.favorite_view.viewport(),
        Qt.MouseButton.LeftButton,
        pos=point,
    )
    QTest.qWait(30)
    assert calls == [str(folder)]
    calls.clear()
    QTest.mouseDClick(
        window.favorite_view.viewport(),
        Qt.MouseButton.LeftButton,
        pos=point,
    )
    QTest.mouseRelease(
        window.favorite_view.viewport(),
        Qt.MouseButton.LeftButton,
        pos=point,
    )
    QTest.qWait(30)
    assert calls == [str(folder)]
    window.close()
    qapp.processEvents()
    store.close()


@pytest.mark.parametrize("wide_name", ["03_wide.webp", "07_wide.webp", "11_wide.webp"])
def test_browser_to_viewer_wide_open_matches_clicked_path(
    qapp,
    tmp_path: Path,
    wide_name: str,
) -> None:
    folder = tmp_path / "wide_fixture"
    folder.mkdir()
    names = [
        "01_portrait.webp",
        "02_portrait.webp",
        "03_wide.webp",
        "04_portrait.webp",
        "05_portrait.webp",
        "06_portrait.webp",
        "07_wide.webp",
        "08_portrait.webp",
        "09_portrait.webp",
        "10_portrait.webp",
        "11_wide.webp",
        "12_portrait.webp",
    ]
    for name in names:
        size = (1600, 700) if "wide" in name else (700, 1100)
        Image.new("RGB", size, "white").save(folder / name, "WEBP")
    (folder / "notes.txt").write_text("unsupported", encoding="utf-8")
    (folder / ".hidden.txt").write_text("hidden", encoding="utf-8")
    (folder / "subfolder").mkdir()
    (folder / "other.cbz").write_bytes(b"not opened")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply(
        {
            "view_mode": "spread",
            "single_first_page": True,
            "reading_direction": "rtl",
            "treat_wide_image_as_single": True,
        }
    )
    store = MetadataStore(tmp_path / "wide_metadata.sqlite3")
    viewer = ViewerWindow(config_manager=config, metadata_store=store)
    browser = BrowserWindow(
        config_manager=config,
        restore_initial_location=False,
        open_path_handler=lambda path, _new, snapshot: viewer.open_path(
            path,
            folder_snapshot=snapshot,
        ),
    )
    browser.current_path = folder
    browser.item_model.set_items(
        [_browser_item(folder / name) for name in names]
        + [
            _browser_item(folder / "subfolder", BrowserItemKind.FOLDER),
            _browser_item(folder / "notes.txt", BrowserItemKind.OTHER),
            _browser_item(folder / ".hidden.txt", BrowserItemKind.OTHER),
            _browser_item(folder / "other.cbz", BrowserItemKind.ARCHIVE),
        ]
    )
    target = folder / wide_name
    browser.open_item(browser.item_model.index(browser.item_model.row_for_path(target), 0))
    target_index = names.index(wide_name)
    deadline = time.monotonic() + 3
    progress = None
    while time.monotonic() < deadline:
        qapp.processEvents()
        progress = store.get_reading_progress(str(folder))
        if (
            viewer.model.get_image_size(target_index) is not None
            and viewer.presentation_state.displayed_page == target_index
            and progress is not None
            and progress.page_index == target_index
        ):
            break
        QTest.qWait(5)
    assert viewer.model.focused_index == target_index
    assert Path(viewer.model.display_path_for_index(viewer.model.focused_index)) == target
    assert [slot.page_index for slot in viewer.model.spread_at().slots] == [target_index]
    assert target.name in viewer.status.currentMessage()
    assert progress is not None
    assert progress.page_index == target_index
    viewer.close()
    browser.close()
    qapp.processEvents()
    store.close()

@pytest.mark.parametrize(
    ("allowed", "default", "modifiers", "expected_action", "expected_operation"),
    [
        (Qt.DropAction.CopyAction, "move", Qt.KeyboardModifier.NoModifier,
         Qt.DropAction.CopyAction, FileOperationKind.COPY),
        (Qt.DropAction.MoveAction, "copy", Qt.KeyboardModifier.NoModifier,
         None, None),
        (Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, "copy",
         Qt.KeyboardModifier.ControlModifier,
         Qt.DropAction.MoveAction, FileOperationKind.MOVE),
    ],
)
def test_favorite_qt_drop_action_matches_operation(
    qapp, tmp_path: Path, monkeypatch, allowed, default, modifiers,
    expected_action, expected_operation,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    source = tmp_path / "source.txt"
    source.write_text("x", encoding="utf-8")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"favorite_drop_default_operation": default,
                  "favorite_drop_confirm_move": False})
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.add_folder_bookmark(str(target))
    window = BrowserWindow(config_manager=config, metadata_store=store,
                           restore_initial_location=False)
    window.show()
    qapp.processEvents()
    operations = []
    monkeypatch.setattr(window, "_start_file_operation",
                        lambda operation, **kwargs: operations.append(operation) or True)
    view = window.favorite_view
    position = view.visualRect(view.model().index(0, 0)).center()
    mime = build_path_mime_data((str(source),))
    enter = QDragEnterEvent(position, allowed, mime,
                            Qt.MouseButton.LeftButton, modifiers)
    qapp.sendEvent(view.viewport(), enter)
    move = QDragMoveEvent(position, allowed, mime,
                          Qt.MouseButton.LeftButton, modifiers)
    qapp.sendEvent(view.viewport(), move)
    drop = QDropEvent(QPointF(position), allowed, mime,
                      Qt.MouseButton.LeftButton, modifiers)
    qapp.sendEvent(view.viewport(), drop)
    if expected_action is None:
        assert not drop.isAccepted()
        assert operations == []
    else:
        assert drop.isAccepted()
        assert drop.dropAction() == expected_action
        assert operations == [expected_operation]
    window.close()
    qapp.processEvents()
    store.close()

def test_shift_sidebar_folder_add_overrides_favorite_operation(
    qapp, tmp_path: Path, monkeypatch,
) -> None:
    target = tmp_path / "target"
    source = tmp_path / "source"
    target.mkdir()
    source.mkdir()
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    assert config.get("sidebar_shift_drop_folders_to_favorites") is True
    config.apply({"sidebar_shift_drop_folders_to_favorites": True,
                  "favorite_drop_default_operation": "move",
                  "favorite_drop_confirm_move": False})
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.add_folder_bookmark(str(target))
    window = BrowserWindow(config_manager=config, metadata_store=store,
                           restore_initial_location=False)
    window.item_model.set_items([_browser_item(source, BrowserItemKind.FOLDER)])
    assert window._shift_favorite_drop_requested(
        Qt.KeyboardModifier.ShiftModifier, window.favorite_view,
    )
    operations = []
    monkeypatch.setattr(window, "_start_file_operation",
                        lambda operation, **kwargs: operations.append(operation) or True)
    index = window.folder_bookmark_model.index(0, 0)
    window._on_favorite_paths_dropped(
        (str(source),), index, Qt.KeyboardModifier.ShiftModifier,
        window.list_view, Qt.DropAction.CopyAction,
    )
    assert str(source) in [item.path for item in store.list_folder_bookmarks()]
    assert not operations
    window._on_favorite_paths_dropped(
        (str(source),), index, Qt.KeyboardModifier.NoModifier,
        window.list_view, Qt.DropAction.MoveAction,
    )
    assert operations == [FileOperationKind.MOVE]
    window.sidebar_shift_drop_folders_to_favorites = False
    window._on_favorite_paths_dropped(
        (str(source),), index, Qt.KeyboardModifier.ShiftModifier,
        window.list_view, Qt.DropAction.MoveAction,
    )
    assert operations == [FileOperationKind.MOVE, FileOperationKind.MOVE]
    window.close()
    qapp.processEvents()
    store.close()


@pytest.mark.parametrize("allowed, accepted", [
    (Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, True),
    (Qt.DropAction.MoveAction, False),
])
def test_shift_external_favorite_drop_never_reports_move(
    qapp, tmp_path: Path, monkeypatch, allowed, accepted,
) -> None:
    target = tmp_path / "target"
    source = tmp_path / "source"
    target.mkdir()
    source.mkdir()
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"sidebar_shift_drop_folders_to_favorites": True,
                  "favorite_drop_default_operation": "move"})
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.add_folder_bookmark(str(target))
    window = BrowserWindow(config_manager=config, metadata_store=store,
                           restore_initial_location=False)
    window.show()
    qapp.processEvents()
    monkeypatch.setattr(window, "_probe_dropped_folders",
                        lambda paths, callback: callback(paths))
    operations = []
    monkeypatch.setattr(window, "_start_file_operation",
                        lambda *args, **kwargs: operations.append(args))
    view = window.favorite_view
    position = view.visualRect(view.model().index(0, 0)).center()
    mime = build_path_mime_data((str(source),))
    enter = QDragEnterEvent(position, allowed, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
    qapp.sendEvent(view.viewport(), enter)
    drop = QDropEvent(QPointF(position), allowed, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
    qapp.sendEvent(view.viewport(), drop)
    assert drop.isAccepted() is accepted
    if accepted:
        assert drop.dropAction() == Qt.DropAction.CopyAction
        assert str(source) in [item.path for item in store.list_folder_bookmarks()]
    else:
        assert str(source) not in [item.path for item in store.list_folder_bookmarks()]
    assert not operations
    window.close()
    qapp.processEvents()
    store.close()


@pytest.mark.parametrize("view_kind", ["tree", "history"])
def test_shift_sidebar_drop_reports_copy_or_rejects_move_only(
    qapp, view_kind: str, tmp_path: Path,
) -> None:
    view = PathDropTreeView() if view_kind == "tree" else SidebarHistoryView()
    view.set_shift_favorite_drop_enabled(True)
    if view_kind == "history":
        view.set_folder_drop_enabled(True)
    mime = build_path_mime_data((str(tmp_path),))
    accepted = QDropEvent(
        QPointF(1, 1), Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
        mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
    )
    resolver = view._accept_tree_drop if view_kind == "tree" else view._accept_history_drop
    assert resolver(accepted)
    assert accepted.dropAction() == Qt.DropAction.CopyAction
    move_only = QDropEvent(
        QPointF(1, 1), Qt.DropAction.MoveAction,
        mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
    )
    assert not resolver(move_only)
    assert not move_only.isAccepted()
    view.close()

def test_internal_favorite_reorder_keeps_move_action(
    qapp, tmp_path: Path, monkeypatch,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.add_folder_bookmark(str(first))
    store.add_folder_bookmark(str(second))
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"sidebar_shift_drop_folders_to_favorites": True,
                  "sidebar_drop_disable_file_operations": True})
    window = BrowserWindow(config_manager=config,
                           metadata_store=store, restore_initial_location=False)
    index = window.folder_bookmark_model.index(1, 0)
    assert window._favorite_drop_qt_action(
        index, Qt.KeyboardModifier.ShiftModifier, window.favorite_view,
    ) == Qt.DropAction.MoveAction
    operations = []
    monkeypatch.setattr(window, "_start_file_operation",
                        lambda *args, **kwargs: operations.append(args))
    window._on_favorite_paths_dropped(
        (str(first),), index, Qt.KeyboardModifier.ShiftModifier,
        window.favorite_view, Qt.DropAction.MoveAction,
    )
    assert [item.path for item in store.list_folder_bookmarks()] == [str(second), str(first)]
    assert not operations
    window.close()
    qapp.processEvents()
    store.close()

def test_shift_file_drop_keeps_file_operation_for_favorite_and_tree(
    qapp, tmp_path: Path, monkeypatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    source = tmp_path / "source.txt"
    source.write_text("x", encoding="utf-8")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"favorite_drop_default_operation": "move",
                  "favorite_drop_confirm_move": False})
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.add_folder_bookmark(str(target))
    window = BrowserWindow(config_manager=config, metadata_store=store,
                           restore_initial_location=False)
    window.item_model.set_items([_browser_item(source, BrowserItemKind.OTHER)])
    operations = []
    monkeypatch.setattr(window, "_start_file_operation",
                        lambda operation, **kwargs: operations.append(operation) or True)
    monkeypatch.setattr(window, "_probe_dropped_folders",
                        lambda paths, callback: callback(()))
    monkeypatch.setattr(window.file_system_model, "filePath",
                        lambda index: str(target))
    favorite_index = window.folder_bookmark_model.index(0, 0)
    tree_index = window.item_model.index(0, 0)
    window._on_favorite_paths_dropped(
        (str(source),), favorite_index, Qt.KeyboardModifier.ShiftModifier,
        window.list_view, Qt.DropAction.MoveAction,
    )
    window._on_favorite_paths_dropped(
        (str(source),), favorite_index, Qt.KeyboardModifier.ShiftModifier,
        None, Qt.DropAction.CopyAction,
    )
    window._on_tree_paths_dropped(
        (str(source),), tree_index, Qt.KeyboardModifier.ShiftModifier,
        window.list_view, Qt.DropAction.MoveAction,
    )
    window._on_tree_paths_dropped(
        (str(source),), tree_index, Qt.KeyboardModifier.ShiftModifier,
        None, Qt.DropAction.CopyAction,
    )
    assert operations == [
        FileOperationKind.MOVE, FileOperationKind.COPY,
        FileOperationKind.MOVE, FileOperationKind.COPY,
    ]
    assert len(store.list_folder_bookmarks()) == 1
    window.close()
    qapp.processEvents()
    store.close()

def test_sidebar_operation_block_priority_and_execution_guard(
    qapp, tmp_path: Path, monkeypatch,
) -> None:
    target = tmp_path / "target"
    ordinary_folder = tmp_path / "ordinary"
    shift_folder = tmp_path / "shift"
    file_path = tmp_path / "file.txt"
    for folder in (target, ordinary_folder, shift_folder):
        folder.mkdir()
    file_path.write_text("x", encoding="utf-8")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    assert config.get("sidebar_drop_disable_file_operations") is False
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.add_folder_bookmark(str(target))
    window = BrowserWindow(config_manager=config, metadata_store=store,
                           restore_initial_location=False)
    operations = []
    monkeypatch.setattr(window, "_start_file_operation",
                        lambda operation, **kwargs: operations.append(operation) or True)
    changed = config.apply({"sidebar_drop_disable_file_operations": True})
    window.apply_settings(changed)
    assert window.folder_tree._sidebar_file_operations_disabled
    assert window.history_view._sidebar_file_operations_disabled
    index = window.folder_bookmark_model.index(0, 0)
    window.item_model.set_items([
        _browser_item(ordinary_folder, BrowserItemKind.FOLDER),
        _browser_item(shift_folder, BrowserItemKind.FOLDER),
        _browser_item(file_path, BrowserItemKind.OTHER),
    ])
    window._on_favorite_paths_dropped(
        (str(ordinary_folder),), index, Qt.KeyboardModifier.NoModifier,
        window.list_view, Qt.DropAction.CopyAction,
    )
    assert len(store.list_folder_bookmarks()) == 1
    window._on_favorite_paths_dropped(
        (str(shift_folder),), index, Qt.KeyboardModifier.ShiftModifier,
        window.list_view, Qt.DropAction.CopyAction,
    )
    assert str(shift_folder) in [item.path for item in store.list_folder_bookmarks()]
    window._on_favorite_paths_dropped(
        (str(file_path),), index, Qt.KeyboardModifier.ShiftModifier,
        window.list_view, Qt.DropAction.CopyAction,
    )
    assert not operations
    assert not window._start_favorite_drop_operation(
        (str(file_path),), target, "target", Qt.KeyboardModifier.NoModifier,
    )
    assert not window._start_drop_operation(
        (str(file_path),), target, Qt.KeyboardModifier.NoModifier,
        sidebar_drop=True,
    )
    assert not operations
    assert window._start_drop_operation(
        (str(file_path),), target, Qt.KeyboardModifier.NoModifier,
    )
    assert operations == [FileOperationKind.MOVE]
    changed = config.apply({"sidebar_drop_folders_to_favorites": True})
    window.apply_settings(changed)
    window._on_favorite_paths_dropped(
        (str(ordinary_folder),), index, Qt.KeyboardModifier.NoModifier,
        window.list_view, Qt.DropAction.CopyAction,
    )
    assert str(ordinary_folder) in [item.path for item in store.list_folder_bookmarks()]
    assert operations == [FileOperationKind.MOVE]
    window.close()
    qapp.processEvents()
    store.close()


@pytest.mark.parametrize("view_kind", ["favorite", "tree", "history"])
def test_sidebar_operation_block_rejects_move_only_qt_drop(
    qapp, view_kind: str, tmp_path: Path,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"sidebar_drop_disable_file_operations": True})
    window = BrowserWindow(config_manager=config, restore_initial_location=False)
    mime = build_path_mime_data((str(tmp_path),))
    if view_kind == "favorite":
        event = QDropEvent(QPointF(1, 1), Qt.DropAction.MoveAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        assert window.favorite_view._accept_path_drop(event, QModelIndex()) is None
    else:
        view = window.folder_tree if view_kind == "tree" else window.history_view
        event = QDropEvent(QPointF(1, 1), Qt.DropAction.MoveAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        resolver = view._accept_tree_drop if view_kind == "tree" else view._accept_history_drop
        assert not resolver(event)
    assert not event.isAccepted()
    window.close()
    qapp.processEvents()

@pytest.mark.parametrize("view_kind", ["favorite", "tree", "history"])
def test_sidebar_block_without_add_condition_ignores_qt_events(
    qapp, view_kind: str, tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    source = tmp_path / "source"
    target.mkdir()
    source.mkdir()
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"sidebar_drop_disable_file_operations": True,
                  "sidebar_shift_drop_folders_to_favorites": False})
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.add_folder_bookmark(str(target))
    window = BrowserWindow(config_manager=config, metadata_store=store,
                           restore_initial_location=False)
    window.show()
    qapp.processEvents()
    view = {"favorite": window.favorite_view, "tree": window.folder_tree,
            "history": window.history_view}[view_kind]
    position = QPoint(2, 2)
    mime = build_path_mime_data((str(source),))
    for event_type in (QDragEnterEvent, QDragMoveEvent):
        event = event_type(position, Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        qapp.sendEvent(view.viewport(), event)
        assert not event.isAccepted()
    drop = QDropEvent(QPointF(position), Qt.DropAction.CopyAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    qapp.sendEvent(view.viewport(), drop)
    assert not drop.isAccepted()
    assert len(store.list_folder_bookmarks()) == 1
    window.close()
    qapp.processEvents()
    store.close()


def test_sidebar_block_global_add_rejects_known_browser_file(
    qapp, tmp_path: Path,
) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"sidebar_drop_disable_file_operations": True,
                  "sidebar_drop_folders_to_favorites": True})
    window = BrowserWindow(config_manager=config, restore_initial_location=False)
    window.item_model.set_items([_browser_item(file_path, BrowserItemKind.OTHER)])
    paths = (str(file_path),)
    assert not window._sidebar_add_candidate(
        paths, Qt.KeyboardModifier.NoModifier, window.list_view,
    )
    assert window._favorite_drop_qt_action(
        QModelIndex(), Qt.KeyboardModifier.NoModifier,
        window.list_view, build_path_mime_data(paths),
    ) == Qt.DropAction.IgnoreAction
    window.close()
    qapp.processEvents()
