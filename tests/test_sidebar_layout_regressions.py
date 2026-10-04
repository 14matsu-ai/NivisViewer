import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QListView, QTabWidget, QTreeView, QWidget

from app.bookmark_model import BookmarkModel
from app.folder_bookmark_model import FolderBookmarkModel
from app.metadata_store import MetadataStore
from app.sidebar_layout import SidebarLayoutController


def _controller(qapp):
    del qapp
    container = QWidget()
    favorites = QListView(container)
    tree = QTreeView(container)
    history = QListView(container)
    books = QListView(container)
    controller = SidebarLayoutController(
        container,
        favorites_view=favorites,
        folder_tree=tree,
        history_view=history,
        bookmarks_view=books,
    )
    return container, favorites, tree, history, books, controller


def test_split_layout_uses_no_splitter_when_only_tree_visible(qapp):
    (
        container,
        _favorites,
        tree,
        _history,
        _books,
        controller,
    ) = _controller(qapp)

    controller.apply(
        "favorites_top_tree_bottom",
        show_favorites=False,
        show_history=False,
        show_tree=True,
    )

    assert controller.splitter is None
    assert container.layout().itemAt(0).widget() is tree


def test_split_layout_uses_no_splitter_when_only_favorites_side_visible(qapp):
    (
        container,
        _favorites,
        _tree,
        _history,
        _books,
        controller,
    ) = _controller(qapp)

    controller.apply(
        "favorites_top_tree_bottom",
        show_favorites=True,
        show_history=True,
        show_tree=False,
    )

    assert controller.splitter is None
    assert container.layout().count() == 1


def test_splitter_sizes_survive_visibility_roundtrip(qapp):
    (
        container,
        _favorites,
        _tree,
        _history,
        _books,
        controller,
    ) = _controller(qapp)

    controller.apply(
        "favorites_top_tree_bottom",
        splitter_sizes=(180, 460),
        show_favorites=True,
        show_history=True,
        show_tree=True,
    )
    container.resize(400, 640)
    container.show()
    qapp.processEvents()
    assert controller.splitter is not None
    controller.splitter.setSizes([240, 400])
    controller._record_splitter_sizes(controller.splitter)
    before = controller.current_splitter_sizes()
    assert 230 <= before[0] <= 250
    assert 390 <= before[1] <= 410

    controller.apply(
        "favorites_top_tree_bottom",
        splitter_sizes=before,
        show_favorites=False,
        show_history=False,
        show_tree=True,
    )
    assert controller.splitter is None
    assert controller.current_splitter_sizes() == before

    controller.apply(
        "favorites_top_tree_bottom",
        splitter_sizes=before,
        show_favorites=True,
        show_history=True,
        show_tree=True,
    )
    assert controller.splitter is not None
    assert controller.current_splitter_sizes() == before


def test_folder_and_book_favorites_show_distinct_saved_entries(tmp_path, qapp):
    folder = tmp_path / "navigation-folder"
    book_folder = tmp_path / "image-book"
    archive = tmp_path / "image-book.zip"
    folder.mkdir()
    book_folder.mkdir()
    archive.write_bytes(b"placeholder")
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.add_folder_bookmark(str(folder))
    store.add_browser_bookmark(str(book_folder), item_type="book_folder")
    store.add_browser_bookmark(str(archive), item_type="archive")

    folder_model = FolderBookmarkModel(store)
    book_model = BookmarkModel(store, book_entries_only=True)
    assert [entry.path for entry in folder_model.entries] == [str(folder)]
    assert [entry.item_type for entry in book_model.entries] == [
        "book_folder", "archive",
    ]
    assert book_model.data(
        book_model.index(0, 0), Qt.ItemDataRole.DecorationRole,
    ) is None

    folder_model.deleteLater()
    book_model.deleteLater()
    qapp.processEvents()
    store.close()
