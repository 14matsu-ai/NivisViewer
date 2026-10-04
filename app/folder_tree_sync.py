from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QModelIndex, QObject, QTimer, Signal
from PySide6.QtWidgets import QFileSystemModel, QTreeView


TREE_SYNC_MODES = {"off", "select_current", "focus_current"}


class FolderTreeSyncController(QObject):
    sync_finished = Signal(int, str)

    def __init__(
        self,
        tree: QTreeView,
        model: QFileSystemModel,
        parent: QObject | None = None,
        *,
        maximum_retries: int = 8,
    ) -> None:
        super().__init__(parent or tree)
        self.tree = tree
        self.model = model
        self.maximum_retries = max(1, int(maximum_retries))
        self.applying = False
        self.generation = 0
        self._target: Path | None = None
        self._mode = "focus_current"
        self._collapse_unrelated = True
        self._focus_rebase = True
        self._context_ancestor_levels = 3
        self._retry_count = 0
        self._auto_expanded: set[str] = set()
        self._user_expanded: set[str] = set()
        model.directoryLoaded.connect(self._on_directory_loaded)
        tree.expanded.connect(self._on_expanded)
        tree.collapsed.connect(self._on_collapsed)

    @property
    def auto_expanded_paths(self) -> frozenset[str]:
        return frozenset(self._auto_expanded)

    @property
    def user_expanded_paths(self) -> frozenset[str]:
        return frozenset(self._user_expanded)

    def sync(
        self,
        path: str | Path,
        *,
        mode: str,
        collapse_unrelated: bool,
        focus_rebase: bool = True,
        context_ancestor_levels: int = 3,
    ) -> int:
        self.generation += 1
        self._target = Path(path)
        self._mode = mode if mode in TREE_SYNC_MODES else "focus_current"
        self._collapse_unrelated = bool(collapse_unrelated)
        self._focus_rebase = bool(focus_rebase)
        self._context_ancestor_levels = max(
            0,
            min(12, int(context_ancestor_levels)),
        )
        self._retry_count = 0
        if self._mode == "off":
            self._target = None
            self.sync_finished.emit(self.generation, str(path))
        else:
            self._attempt(self.generation)
        return self.generation

    def cancel(self) -> None:
        self.generation += 1
        self._target = None

    def show_full_tree(self, *, keep_current: bool = True) -> None:
        """Restore the model root without navigating or taking keyboard focus."""

        self.tree.setRootIndex(QModelIndex())
        if not keep_current:
            return
        index = self.tree.currentIndex()
        if index.isValid():
            self.tree.scrollTo(index, QTreeView.ScrollHint.PositionAtCenter)
            self.tree.horizontalScrollBar().setValue(0)

    def _attempt(self, generation: int) -> None:
        if generation != self.generation or self._target is None:
            return
        index = self.model.index(str(self._target))
        if not index.isValid():
            self._retry_count += 1
            if self._retry_count <= self.maximum_retries:
                QTimer.singleShot(50, lambda: self._attempt(generation))
            else:
                target = str(self._target)
                self._target = None
                self.sync_finished.emit(generation, target)
            return

        ancestor_indexes: list[QModelIndex] = []
        parent = index.parent()
        while parent.isValid():
            ancestor_indexes.append(parent)
            parent = parent.parent()
        ancestor_keys = {
            self._path_key(self.model.filePath(ancestor))
            for ancestor in ancestor_indexes
        }
        self.applying = True
        set_programmatic_sync = getattr(
            self.tree,
            "set_programmatic_sync",
            None,
        )
        if callable(set_programmatic_sync):
            set_programmatic_sync(True)
        try:
            if self._mode == "focus_current" and self._focus_rebase:
                rebase_index = self._focused_root_index(
                    index,
                    ancestor_indexes,
                    self._context_ancestor_levels,
                )
                self.tree.setRootIndex(rebase_index)
            else:
                self.tree.setRootIndex(QModelIndex())
            if (
                self._mode == "focus_current"
                and self._collapse_unrelated
            ):
                for path_key in tuple(self._auto_expanded):
                    if path_key in ancestor_keys or path_key in self._user_expanded:
                        continue
                    old_index = self.model.index(path_key)
                    if old_index.isValid():
                        self.tree.collapse(old_index)
                    self._auto_expanded.discard(path_key)
            for ancestor in reversed(ancestor_indexes):
                key = self._path_key(self.model.filePath(ancestor))
                if not self.tree.isExpanded(ancestor):
                    self.tree.expand(ancestor)
                    self._auto_expanded.add(key)
            self.tree.setCurrentIndex(index)
            if self._mode == "focus_current":
                self.tree.scrollTo(index, QTreeView.ScrollHint.PositionAtCenter)
                self.tree.horizontalScrollBar().setValue(0)
            else:
                self.tree.scrollTo(index, QTreeView.ScrollHint.EnsureVisible)
        finally:
            if callable(set_programmatic_sync):
                set_programmatic_sync(False)
            self.applying = False
        self._target = None
        self.sync_finished.emit(generation, str(self.model.filePath(index)))

    @staticmethod
    def _focused_root_index(
        current: QModelIndex,
        ancestors_nearest_first: list[QModelIndex],
        levels: int,
    ) -> QModelIndex:
        # Context count includes the current folder, matching the UI example:
        # levels=3 shows root/current-grandparent -> parent -> current.
        parent_steps = max(0, int(levels) - 1)
        if parent_steps == 0 or not ancestors_nearest_first:
            return current
        return ancestors_nearest_first[
            min(parent_steps - 1, len(ancestors_nearest_first) - 1)
        ]

    def _on_directory_loaded(self, _path: str) -> None:
        if self._target is not None and self._mode != "off":
            self._attempt(self.generation)

    def _on_expanded(self, index: QModelIndex) -> None:
        if self.applying:
            return
        path = self.model.filePath(index)
        if path:
            self._user_expanded.add(self._path_key(path))

    def _on_collapsed(self, index: QModelIndex) -> None:
        if self.applying:
            return
        path = self.model.filePath(index)
        if path:
            key = self._path_key(path)
            self._user_expanded.discard(key)
            self._auto_expanded.discard(key)

    @staticmethod
    def _path_key(path: str | Path) -> str:
        return os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(path)))
        ).casefold()
