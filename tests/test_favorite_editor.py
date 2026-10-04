from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QHelpEvent, QIcon, QImage, QMouseEvent, QPainter, QPalette, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QColorDialog, QDialog, QDialogButtonBox, QStyle, QStyleOptionViewItem, QToolTip

from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.favorite_editor_dialog import FavoriteEditorDialog
from app.favorite_item_delegate import FavoriteItemDelegate
from app.metadata_store import MetadataStore
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.explorer_list_view import ExplorerListView


def test_favorite_color_defaults_and_separator_editor(tmp_path: Path, qapp) -> None:
    config = ConfigManager(tmp_path / "config.json")
    values = config.load()
    assert values["favorite_color_show_icon"] is True
    assert values["favorite_color_show_left_bar"] is True
    assert values["favorite_color_show_background"] is False
    assert values["favorite_color_show_text"] is False
    assert values["favorite_color_background_transparency"] == 68

    folder = tmp_path / "folder"
    folder.mkdir()
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    try:
        assert store.add_group_favorite(1, str(folder))
        dialog = FavoriteEditorDialog(store.list_group_favorites(1), {}, [])
        try:
            dialog._add_separator()
            separators = dialog.separators()
            assert len(separators) == 1
            assert separators[0]["alignment"] == "center"
            assert separators[0]["style"] == "standard"
            dialog.separator_style.setCurrentIndex(dialog.separator_style.findData("compact"))
            assert dialog.separators()[0]["style"] == "compact"
        finally:
            dialog.reject()
    finally:
        store.close()


def test_favorite_accent_and_separator_render_with_dark_palette(qapp) -> None:
    class ItemModel(QStandardItemModel):
        KindRole = int(Qt.ItemDataRole.UserRole) + 1
        AccentColorRole = KindRole + 1
        SeparatorAlignmentRole = AccentColorRole + 1
        SeparatorStyleRole = SeparatorAlignmentRole + 1

    model = ItemModel()
    item = QStandardItem("Folder")
    icon_pixmap = QPixmap(16, 16)
    icon_pixmap.fill(QColor("#ffffff"))
    item.setIcon(QIcon(icon_pixmap))
    item.setData("folder", model.KindRole)
    item.setData("#0088ff", model.AccentColorRole)
    model.appendRow(item)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 180, 24)
    option.state = QStyle.StateFlag.State_Enabled
    option.palette = QPalette()
    option.palette.setColor(QPalette.ColorRole.Base, QColor("#202020"))
    option.palette.setColor(QPalette.ColorRole.Text, QColor("#ffffff"))
    delegate = FavoriteItemDelegate(
        show_color_icon=True, show_color_left_bar=False,
        show_color_background=True, show_color_text=False,
    )
    image = QImage(180, 24, QImage.Format.Format_ARGB32)
    image.fill(QColor("#202020"))
    painter = QPainter(image)
    delegate.paint(painter, option, model.index(0, 0))
    painter.end()
    background = image.pixelColor(140, 12)
    icon_color = image.pixelColor(10, 12)
    assert background.blue() > background.red() + 30
    assert icon_color.blue() > icon_color.red() + 80

    option.state |= QStyle.StateFlag.State_Selected
    option.palette.setColor(QPalette.ColorRole.Highlight, QColor("#555555"))
    option.palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    selected_image = QImage(180, 24, QImage.Format.Format_ARGB32)
    selected_image.fill(QColor("#202020"))
    painter = QPainter(selected_image)
    delegate.paint(painter, option, model.index(0, 0))
    painter.end()
    selected_background = selected_image.pixelColor(140, 12)
    assert selected_background.blue() > selected_background.red() + 30

    separator = QStandardItem("区切り")
    separator.setData("separator", model.KindRole)
    separator.setData("compact", model.SeparatorStyleRole)
    separator.setData("#ff4060", model.AccentColorRole)
    model.appendRow(separator)
    compact_index = model.index(1, 0)
    assert delegate.sizeHint(option, compact_index).height() == 9
    compact_option = QStyleOptionViewItem(option)
    compact_option.rect = QRect(0, 0, 180, 9)
    compact_option.state = QStyle.StateFlag.State_Enabled
    compact_image = QImage(180, 9, QImage.Format.Format_ARGB32)
    compact_image.fill(QColor("#202020"))
    painter = QPainter(compact_image)
    delegate.paint(painter, compact_option, compact_index)
    painter.end()
    assert compact_image.pixelColor(10, 4) != QColor("#202020")
    assert compact_image.pixelColor(10, 4).red() > compact_image.pixelColor(10, 4).blue()
    assert compact_image.pixelColor(10, 3) == compact_image.pixelColor(10, 5)
    separator.setData("standard", model.SeparatorStyleRole)
    option.state = QStyle.StateFlag.State_Enabled
    standard_image = QImage(180, 24, QImage.Format.Format_ARGB32)
    standard_image.fill(QColor("#202020"))
    painter = QPainter(standard_image)
    delegate.paint(painter, option, compact_index)
    painter.end()
    assert max(
        standard_image.pixelColor(x, y).red()
        for x in range(65, 115) for y in range(3, 21)
    ) > 230


def test_favorite_reorder_and_arrow_move_skip_separator_rows(tmp_path: Path, qapp) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    store.add_folder_bookmark(str(first))
    store.add_folder_bookmark(str(second))
    config.apply({"favorite_separators": {"1": [
        {"id": "divider", "label": "区切り", "alignment": "center", "before_path": str(second)},
    ]}})
    window = BrowserWindow(config_manager=config, metadata_store=store, restore_initial_location=False)
    try:
        assert [item.kind for item in window.folder_bookmark_model.entries] == ["folder", "separator", "folder"]
        second_index = window.folder_bookmark_model.index(2, 0)
        window._on_favorite_paths_dropped(
            (str(first),), second_index, Qt.KeyboardModifier.NoModifier,
            window.favorite_view, Qt.DropAction.MoveAction,
        )
        assert [item.path for item in store.list_folder_bookmarks()] == [str(second), str(first)]
        assert not window.move_folder_bookmark(window.folder_bookmark_model.index(0, 0), 1)
        assert window.move_folder_bookmark(window.folder_bookmark_model.index(1, 0), 1)
        assert [item.path for item in store.list_folder_bookmarks()] == [str(first), str(second)]
    finally:
        window.close()
        store.close()


def test_favorite_global_color_display_controls_rendering(tmp_path: Path, qapp) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    store.add_folder_bookmark(str(folder))
    config.apply({
        "favorite_item_colors": {"1": {str(folder): "#0088ff"}},
        "favorite_color_show_icon": False,
        "favorite_color_show_left_bar": False,
        "favorite_color_show_background": True,
    })
    window = BrowserWindow(config_manager=config, metadata_store=store, restore_initial_location=False)
    try:
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 180, 24)
        option.state = QStyle.StateFlag.State_Enabled
        option.palette = QPalette()
        option.palette.setColor(QPalette.ColorRole.Base, QColor("#202020"))
        option.palette.setColor(QPalette.ColorRole.Text, QColor("#ffffff"))
        index = window.folder_bookmark_model.index(0, 0)

        def background_color() -> QColor:
            image = QImage(180, 24, QImage.Format.Format_ARGB32)
            image.fill(QColor("#202020"))
            painter = QPainter(image)
            window.favorite_item_delegate.paint(painter, option, index)
            painter.end()
            return image.pixelColor(140, 12)

        enabled = background_color()
        assert enabled.blue() > enabled.red() + 30
        window.favorite_item_delegate.configure_color_display(
            icon=False, left_bar=False, background=False, text=False,
        )
        disabled = background_color()
        assert disabled.blue() <= disabled.red() + 5
    finally:
        window.close()
        store.close()


def test_favorite_editor_apply_saves_without_accepting_dialog(tmp_path: Path, qapp, monkeypatch) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    store.add_folder_bookmark(str(folder))
    window = BrowserWindow(config_manager=config, metadata_store=store, restore_initial_location=False)
    monkeypatch.setattr(QColorDialog, "getColor", lambda *_args: QColor("#ff4060"))

    def apply_then_cancel(dialog: FavoriteEditorDialog):
        dialog._add_separator()
        assert not dialog.color_display_group.isEnabled()
        dialog.show_background.click()
        assert not dialog.show_background.isChecked()
        dialog._choose_color()
        dialog.separator_style.setCurrentIndex(dialog.separator_style.findData("compact"))
        dialog.buttons.button(QDialogButtonBox.StandardButton.Apply).click()
        assert dialog.result() != QDialog.DialogCode.Accepted
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(FavoriteEditorDialog, "exec", apply_then_cancel)
    try:
        window.edit_favorites()
        separators = config.get("favorite_separators")["1"]
        assert len(separators) == 1
        assert separators[0]["style"] == "compact"
        assert separators[0]["color"] == "#ff4060"
        assert config.get("favorite_color_show_background") is False
        assert any(item.separator_style == "compact" for item in window.folder_bookmark_model.entries)
    finally:
        window.close()
        store.close()


def test_compact_separator_uses_less_sidebar_height(tmp_path: Path, qapp) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    store.add_folder_bookmark(str(folder))
    config.apply({"favorite_separators": {"1": [
        {"id": "compact", "label": "", "alignment": "center", "style": "compact", "before_path": str(folder)},
    ]}})
    window = BrowserWindow(config_manager=config, metadata_store=store, restore_initial_location=False)
    try:
        view = window.favorite_view
        view.resize(250, 100)
        view.doItemsLayout()
        separator_height = view.visualRect(window.folder_bookmark_model.index(0, 0)).height()
        folder_height = view.visualRect(window.folder_bookmark_model.index(1, 0)).height()
        assert separator_height > 0
        assert separator_height < folder_height
    finally:
        window.close()
        store.close()


def test_accent_bar_yields_to_selection_and_background_transparency(qapp) -> None:
    class ItemModel(QStandardItemModel):
        AccentColorRole = int(Qt.ItemDataRole.UserRole) + 1

    model = ItemModel()
    item = QStandardItem("Folder")
    item.setData("#ff0000", model.AccentColorRole)
    model.appendRow(item)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 180, 24)
    option.state = QStyle.StateFlag.State_Enabled
    option.palette.setColor(QPalette.ColorRole.Base, QColor("#202020"))
    option.palette.setColor(QPalette.ColorRole.Highlight, QColor("#0088ff"))
    delegate = FavoriteItemDelegate(show_color_background=True)

    def render() -> QImage:
        image = QImage(180, 24, QImage.Format.Format_ARGB32)
        image.fill(QColor("#202020"))
        painter = QPainter(image)
        delegate.paint(painter, option, model.index(0, 0))
        painter.end()
        return image

    assert render().pixelColor(2, 12) == QColor("#ff0000")
    option.state |= QStyle.StateFlag.State_Selected
    selected = render()
    delegate.show_color_left_bar = False
    assert render() == selected
    option.state = QStyle.StateFlag.State_Enabled
    delegate.background_transparency = 0
    assert render().pixelColor(140, 12) == QColor("#ff0000")
    delegate.background_transparency = 100
    assert render().pixelColor(140, 12) == QColor("#202020")


def test_background_transparency_apply_persists_and_reaches_delegate(tmp_path: Path, qapp) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    window = BrowserWindow(config_manager=config, metadata_store=store, restore_initial_location=False)
    dialog = FavoriteEditorDialog([], {}, [], color_display={"background": True})
    try:
        dialog.background_transparency.setValue(25)
        window._save_favorite_editor(dialog, 1)
        assert window.favorite_item_delegate.background_transparency == 25
        reloaded = ConfigManager(tmp_path / "config.json")
        assert reloaded.load()["favorite_color_background_transparency"] == 25
        reloaded.apply({"favorite_color_background_transparency": 200})
        assert reloaded.get("favorite_color_background_transparency") == 100
    finally:
        dialog.reject()
        window.close()
        store.close()


def test_sidebar_path_tooltips_hide_on_movement_and_thumbnail_notices_remain(tmp_path: Path, qapp, monkeypatch) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.add_folder_bookmark(str(folder))
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = BrowserWindow(config_manager=config, metadata_store=store, restore_initial_location=False)
    try:
        index = window.folder_bookmark_model.index(0, 0)
        assert index.data(Qt.ItemDataRole.ToolTipRole) == str(folder)
        path = folder / "image.png"
        model = BrowserItemModel()
        model.set_items([BrowserItem(path=path, kind=BrowserItemKind.IMAGE, display_name=path.name, modified_at=None)])
        index = model.index(0, 0)
        assert index.data(Qt.ItemDataRole.ToolTipRole) is None
        model.set_thumbnail_error(path, "decode failed")
        assert "decode failed" in index.data(Qt.ItemDataRole.ToolTipRole)
        model.set_items([BrowserItem(path=path, kind=BrowserItemKind.IMAGE, display_name=path.name, modified_at=None, online_only=True)])
        assert model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole)
        hidden = []
        monkeypatch.setattr(QToolTip, "hideText", lambda: hidden.append(True))
        for view in (window.favorite_view, window.history_view, window.folder_tree, window.list_view):
            assert view.hasMouseTracking()
            hidden.clear()
            move = QMouseEvent(
                QEvent.Type.MouseMove, QPointF(10, 10), QPointF(10, 10),
                Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            )
            qapp.sendEvent(view.viewport(), move)
            assert hidden
            hidden.clear()
            qapp.sendEvent(view.viewport(), QEvent(QEvent.Type.Leave))
            assert hidden
    finally:
        window.close()
        store.close()


def test_qt_tooltip_events_cannot_revive_tip_before_mouse_stops(qapp, monkeypatch) -> None:
    view = ExplorerListView()
    model = QStandardItemModel(view)
    for name in ("first", "second"):
        item = QStandardItem(name)
        item.setToolTip(f"C:/{name}")
        model.appendRow(item)
    view.setModel(model)
    view.resize(200, 100)
    view._item_tooltips._timer.setInterval(80)
    shown = []
    monkeypatch.setattr(QToolTip, "showText", lambda _pos, text, _widget, rect: shown.append((text, rect)))
    monkeypatch.setattr(QToolTip, "hideText", lambda: None)
    # Keep this timer/input test independent of platform-generated enter/leave
    # events from other offscreen test windows. Inject only our own movement.
    view.doItemsLayout()
    monkeypatch.setattr(view.viewport(), "isVisible", lambda: True)
    timer = view._item_tooltips._timer
    expired = QSignalSpy(timer.timeout)

    def move_and_send_qt_tooltip(row):
        point = view.visualRect(model.index(row, 0)).center()
        global_point = view.viewport().mapToGlobal(point)
        move = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(point), QPointF(global_point),
            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        )
        qapp.sendEvent(view.viewport(), move)
        assert timer.isActive()
        assert timer.interval() == 80
        assert view._item_tooltips._position == point
        timer_id = timer.timerId()
        qapp.sendEvent(view.viewport(), QHelpEvent(QEvent.Type.ToolTip, point, global_point))
        # Qt's reactivation event cannot show a tip or replace the movement
        # timer. Check state rather than assuming 30 ms of wall-clock wait
        # stays below the 80 ms timer under system load.
        assert timer.isActive() and timer.timerId() == timer_id

    def wait_for_expiry(count):
        assert expired.wait(1000), "movement timer did not emit timeout"
        assert expired.count() == count
        assert not timer.isActive()

    try:
        move_and_send_qt_tooltip(0)
        assert shown == []
        wait_for_expiry(1)
        assert shown == [("C:/first", view.visualRect(model.index(0, 0)))]
        shown.clear()
        # Simulate Qt's short reactivation delay after a previously visible tip.
        move_and_send_qt_tooltip(1)
        assert shown == []
        previous_timer_id = timer.timerId()
        move_and_send_qt_tooltip(0)
        assert timer.timerId() != previous_timer_id
        assert shown == []
        move_and_send_qt_tooltip(1)
        assert shown == []
        wait_for_expiry(2)
        assert shown == [("C:/second", view.visualRect(model.index(1, 0)))]
        shown.clear()
        move_and_send_qt_tooltip(0)
        qapp.sendEvent(view.viewport(), QEvent(QEvent.Type.Leave))
        assert not timer.isActive()
        assert view._item_tooltips._position is None
        # An already queued timeout must also be harmless after Leave.
        timer.timeout.emit()
        assert shown == []
    finally:
        view.close()
