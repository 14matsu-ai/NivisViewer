from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

from app.browser_item_delegate import (
    BrowserItemDelegate,
    GRID_PRESET_THUMBNAIL_SIZES,
    thumbnail_rect_for_cell,
)
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_sort import BrowserDisplayDensity
from app.config_manager import ConfigManager
from app.folder_bookmark_model import FolderBookmarkModel
from app.metadata_store import MetadataStore


def test_sprint15_config_defaults_and_ranges(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "fullscreen_edge_trigger_px": 100,
                "fullscreen_ui_hide_delay_ms": 1,
                "browser_item_spacing": -1,
                "browser_cell_padding": 99,
                "browser_sidebar_splitter_sizes": [-10, 9000],
                "browser_sidebar_layout": "invalid",
                "folder_tree_sync_mode": "invalid",
            }
        ),
        encoding="utf-8",
    )

    settings = ConfigManager(path).load()

    assert settings["fullscreen_auto_reveal_ui"] is True
    assert settings["fullscreen_edge_trigger_px"] == 32
    assert settings["fullscreen_ui_hide_delay_ms"] == 1
    assert settings["browser_item_spacing"] == 0
    assert settings["browser_cell_padding"] == 12
    assert settings["browser_sidebar_splitter_sizes"] == [40, 4000]
    assert settings["browser_sidebar_layout"] == "favorites_top_tree_bottom"
    assert settings["folder_tree_sync_mode"] == "focus_current"


def test_extra_compact_profile_is_96px_and_uses_one_title_line() -> None:
    delegate = BrowserItemDelegate(
        thumbnail_size=96,
        density=BrowserDisplayDensity.EXTRA_COMPACT,
    )

    assert (
        GRID_PRESET_THUMBNAIL_SIZES[BrowserDisplayDensity.EXTRA_COMPACT]
        == 96
    )
    assert delegate.profile.font_size in {7, 8}
    assert delegate.profile.spacing in {0, 1}
    assert delegate.profile.title_lines == 1


def test_cell_padding_is_internal_and_does_not_change_thumbnail_size() -> None:
    unpadded = BrowserItemDelegate(
        thumbnail_size=96,
        density=BrowserDisplayDensity.EXTRA_COMPACT,
        cell_padding=0,
    )
    padded = BrowserItemDelegate(
        thumbnail_size=96,
        density=BrowserDisplayDensity.EXTRA_COMPACT,
        cell_padding=12,
    )
    unpadded_rect = thumbnail_rect_for_cell(
        QRect(0, 0, unpadded.cell_size.width(), unpadded.cell_size.height()),
        unpadded.frame_size,
        unpadded.cell_padding,
    )
    padded_rect = thumbnail_rect_for_cell(
        QRect(0, 0, padded.cell_size.width(), padded.cell_size.height()),
        padded.frame_size,
        padded.cell_padding,
    )

    assert unpadded_rect.size() == padded_rect.size()
    assert padded.cell_size.width() - unpadded.cell_size.width() == 24
    assert padded.cell_size.height() - unpadded.cell_size.height() == 24
    assert padded_rect.top() == 12


def test_delegate_does_not_retain_selection_or_hover_identity() -> None:
    delegate = BrowserItemDelegate()

    retained_names = {
        "selected_path",
        "current_path",
        "hover_path",
        "focused_path",
        "current_index",
        "hovered_index",
    }
    assert retained_names.isdisjoint(vars(delegate))


def test_delegate_redraw_removes_old_current_focus_but_keeps_selection(
    tmp_path: Path,
    qapp,
) -> None:
    model = BrowserItemModel()
    model.set_items(
        [
            BrowserItem(
                "A",
                tmp_path / "A.jpg",
                BrowserItemKind.IMAGE,
                None,
            ),
            BrowserItem(
                "B",
                tmp_path / "B.jpg",
                BrowserItemKind.IMAGE,
                None,
            ),
        ]
    )
    delegate = BrowserItemDelegate(
        thumbnail_size=96,
        density=BrowserDisplayDensity.EXTRA_COMPACT,
    )
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#0066cc"))
    palette.setColor(
        QPalette.ColorRole.HighlightedText,
        QColor("#ff00ff"),
    )

    def render(state: QStyle.StateFlag) -> tuple[QImage, QRect]:
        size = delegate.cell_size
        canvas = QImage(
            size,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        canvas.fill(QColor("#ffffff"))
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, size.width(), size.height())
        option.palette = palette
        option.state = QStyle.StateFlag.State_Enabled | state
        painter = QPainter(canvas)
        delegate.paint(painter, option, model.index(0, 0))
        painter.end()
        thumbnail = thumbnail_rect_for_cell(
            option.rect,
            delegate.frame_size,
            delegate.cell_padding,
        )
        return canvas, thumbnail

    current, current_rect = render(
        QStyle.StateFlag.State_Selected
        | QStyle.StateFlag.State_HasFocus
    )
    selected_only, selected_rect = render(
        QStyle.StateFlag.State_Selected
    )
    unselected, unselected_rect = render(QStyle.StateFlag.State_None)

    focus_point = current_rect.topLeft() + QPoint(4, 4)
    selection_point = current_rect.topLeft() + QPoint(1, 1)
    assert current.pixelColor(focus_point) == QColor("#ff00ff")
    assert selected_only.pixelColor(focus_point) != QColor("#ff00ff")
    assert selected_only.pixelColor(selection_point) == QColor("#0066cc")
    assert unselected.pixelColor(selection_point) != QColor("#0066cc")
    assert selected_rect == unselected_rect == current_rect


def test_folder_bookmark_crud_reorder_and_non_folder_preservation(
    tmp_path: Path,
) -> None:
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    archive = tmp_path / "book.cbz"
    archive.write_bytes(b"archive")
    first = tmp_path / "日本語"
    second = tmp_path / "二番目"
    first.mkdir()
    second.mkdir()
    store.add_browser_bookmark(
        str(archive),
        label="書庫",
        item_type="archive",
    )

    assert store.add_folder_bookmark(str(first), label="最初")
    assert store.add_folder_bookmark(str(second), label="次")
    assert not store.add_folder_bookmark(str(first), label="重複")
    assert store.rename_bookmark_label(str(first), "日本語ラベル")
    assert store.reorder_folder_bookmarks([str(second), str(first)])

    folders = store.list_folder_bookmarks()
    assert [entry.path for entry in folders] == [
        str(second.absolute()),
        str(first.absolute()),
    ]
    assert folders[1].label == "日本語ラベル"
    assert any(
        entry.path == str(archive.absolute())
        and entry.item_type == "archive"
        for entry in store.list_browser_bookmarks()
    )
    assert store.remove_folder_bookmark(str(second))
    store.close()

    reopened = MetadataStore(tmp_path / "metadata.sqlite3")
    assert [entry.label for entry in reopened.list_folder_bookmarks()] == [
        "日本語ラベル"
    ]
    assert any(
        entry.item_type == "archive"
        for entry in reopened.list_browser_bookmarks()
    )
    reopened.close()


def test_adding_unc_folder_bookmark_does_not_stat_the_target(
    tmp_path: Path,
) -> None:
    store = MetadataStore(tmp_path / "metadata.sqlite3")

    with patch.object(
        Path,
        "stat",
        side_effect=AssertionError("bookmark registration must not stat"),
    ):
        assert store.add_folder_bookmark(
            r"\\server.example\共有\漫画",
            label="UNC",
        )

    assert store.list_folder_bookmarks()[0].label == "UNC"
    store.close()


def test_folder_bookmark_model_probes_missing_paths_off_model_refresh(
    tmp_path: Path,
    qapp,
) -> None:
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    existing = tmp_path / "存在する"
    missing = tmp_path / "見つからない"
    existing.mkdir()
    store.add_folder_bookmark(str(existing), label="存在")
    store.add_folder_bookmark(str(missing), label="欠損")

    model = FolderBookmarkModel(store)
    for _ in range(40):
        qapp.processEvents()
        if all(entry.exists is not None for entry in model.entries):
            break
        QTest.qWait(10)

    assert model.rowCount() == 2
    assert model.entry_at(model.row_for_path(existing)).exists is True
    missing_row = model.row_for_path(missing)
    assert model.entry_at(missing_row).exists is False
    assert "見つかりません" in str(
        model.data(model.index(missing_row, 0), Qt.ItemDataRole.DisplayRole)
    )
    store.close()
