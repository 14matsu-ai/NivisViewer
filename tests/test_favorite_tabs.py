import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QListView, QTreeView, QWidget

from app.favorite_tabs import FavoriteTabs
from app.folder_bookmark_model import FolderBookmarkModel
from app.metadata_store import MetadataStore
from app.sidebar_layout import SidebarLayoutController


def test_group_persistence_isolation_and_deletion(tmp_path, qapp):
    path = str(tmp_path / "画像")
    db = tmp_path / "metadata.db"
    store = MetadataStore(db)
    store.add_folder_bookmark(path, label="既存")
    second = store.create_favorite_group("フォルダ2")
    assert store.add_group_favorite(second, path, label="別名")
    assert not store.add_group_favorite(second, path)
    assert store.rename_group_favorite(second, path, "変更")
    assert store.list_group_favorites(1)[0].label == "既存"
    assert store.list_group_favorites(second)[0].label == "変更"
    assert store.rename_favorite_group(second, "仕事")
    store.close()

    store = MetadataStore(db)
    assert store.list_favorite_groups() == [(1, "フォルダ"), (second, "仕事")]
    assert store.list_group_favorites(second)[0].label == "変更"
    assert store.delete_favorite_group(1)
    assert not store.delete_favorite_group(second)
    store.close()
    store = MetadataStore(db)
    assert store.list_favorite_groups() == [(second, "仕事")]
    assert len(store.list_group_favorites(second)) == 1
    store.close()

def test_group_favorites_reorder_and_follow_relocation(tmp_path, qapp):
    store = MetadataStore(tmp_path / "metadata.db")
    group = store.create_favorite_group("仕事")
    first, second, moved = (str(tmp_path / name) for name in ("一", "二", "移動先"))
    store.add_group_favorite(group, first)
    store.add_group_favorite(group, second)
    store.reorder_group_favorites(group, [second, first])
    assert [item.path for item in store.list_group_favorites(group)] == [second, first]
    store.set_rating(moved, 3)  # Existing destination exercises identity merging.
    assert store.relocate_item(first, moved)
    assert [item.path for item in store.list_group_favorites(group)] == [second, moved]
    assert store.remove_group_favorite(group, second)
    assert [item.path for item in store.list_group_favorites(group)] == [moved]
    store.close()



@pytest.mark.parametrize("layout", ["tabs", "favorites_top_tree_bottom", "favorites_only"])
def test_tabs_survive_layout_changes_and_shrink(tmp_path, qapp, layout):
    store = MetadataStore(tmp_path / "metadata.db")
    container = QWidget()
    view = QListView(container)
    model = FolderBookmarkModel(store, view)
    view.setModel(model)
    tabs = FavoriteTabs(view, store, container)
    tabs.group_changed.connect(model.set_group)
    history = QListView(container)
    controller = SidebarLayoutController(container, favorites_view=tabs,
                                         folder_tree=QTreeView(container), history_view=history)
    controller.apply(layout, show_history=False)
    if controller.tabs:
        controller.tabs.setCurrentWidget(tabs)
    container.resize(600, 600)
    container.show()
    tabs.add_group()
    selected = tabs.group_id
    store.add_group_favorite(selected, str(tmp_path / "second"))
    assert model.rowCount() == 1
    tabs.rename_group(tabs.currentIndex(), "とても長いお気に入りのタブ名")
    qapp.processEvents()
    before = tabs.tabBar().tabRect(tabs.currentIndex()).width()
    tabs.rename_group(tabs.currentIndex(), "短")
    qapp.processEvents()
    assert tabs.tabBar().tabRect(tabs.currentIndex()).width() < before
    for visible in (True, False, True):
        controller.apply(layout, show_history=visible)
        qapp.processEvents()
        assert tabs.group_id == selected
        assert view.isVisible()
        assert model.rowCount() == 1
        assert all(tabs.tabText(i) != "本" for i in range(tabs.count()))
    tabs.setCurrentIndex(0)
    assert model.rowCount() == 0
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    qapp.sendEvent(tabs.tabBar(), event)
    assert tabs.group_id == selected
    assert model.rowCount() == 1
    tabs.delete_group(tabs.currentIndex())
    assert model.rowCount() == 0
    container.close()
    container.deleteLater()
    qapp.processEvents()
    store.close()
