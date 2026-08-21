from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject

from .page_model import PageModel
from .viewer_navigation_policy import NavigationInputKind


PageChangedCallback = Callable[[int, NavigationInputKind], None]


class ViewerPageNavigationController(QObject):
    """Own page-only navigation without any book-opening capability."""

    def __init__(
        self,
        model: PageModel,
        on_page_changed: PageChangedCallback,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.model = model
        self._on_page_changed = on_page_changed

    def next_display_unit(
        self,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> bool:
        return self._move(self.model.next, input_kind=input_kind)

    def previous_display_unit(
        self,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> bool:
        return self._move(self.model.previous, input_kind=input_kind)

    def next_single_page(
        self,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> bool:
        return self._move(self.model.next_single, input_kind=input_kind)

    def previous_single_page(
        self,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> bool:
        return self._move(self.model.previous_single, input_kind=input_kind)

    def first_page(
        self,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> bool:
        return self._move(self.model.first, input_kind=input_kind)

    def last_page(
        self,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> bool:
        return self._move(self.model.last, input_kind=input_kind)

    def go_to_focused_page_index(
        self,
        index: int,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> bool:
        return self._move(
            lambda: self.model.go_to_focused_page_index(index),
            input_kind=input_kind,
        )

    def _move(
        self,
        operation: Callable[[], None],
        *,
        input_kind: NavigationInputKind,
    ) -> bool:
        if self.model.total_pages <= 0:
            return False
        previous_focus = self.model.focused_index
        previous_anchor = self.model.current_index
        operation()
        if (
            self.model.focused_index == previous_focus
            and self.model.current_index == previous_anchor
        ):
            return False
        self._on_page_changed(previous_focus, NavigationInputKind(input_kind))
        return True
