from PySide6.QtWidgets import QListView, QTreeView, QWidget

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
