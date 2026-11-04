"""GUI-owned identities for adjacent searches and committed side navigation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from weakref import ReferenceType

from .adjacent_book_search import AdjacentBookBrowserSnapshot


@dataclass(frozen=True, slots=True)
class BookOpenFence:
    generation: int
    source_identity: int
    open_generation: int

    @classmethod
    def capture(cls, session) -> BookOpenFence:
        return cls(session.generation, id(session.source), session._open_generation)

    def matches(self, session) -> bool:
        return (self.generation == session.generation
                and self.source_identity == id(session.source)
                and self.open_generation == session._open_generation)


@dataclass(frozen=True, slots=True)
class AdjacentSearchContext:
    window_ref: ReferenceType[object]
    generation: int
    direction: int
    expected_path_key: str
    operation: Literal['book', 'viewer_folder', 'browser_folder']
    fence: BookOpenFence | None = None
    snapshot: AdjacentBookBrowserSnapshot | None = None
    attempted_paths: frozenset[str] = frozenset()
    window_id: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'window_id', id(self.window_ref()))


@dataclass(frozen=True, slots=True)
class SideNavigationAnchor:
    path: str
    snapshot: AdjacentBookBrowserSnapshot | None
    generation: int
    source_identity: int

    def matches(self, session) -> bool:
        return (self.generation == session.generation
                and self.source_identity == id(session.source))


@dataclass(frozen=True, slots=True)
class PendingSideNavigation:
    open_generation: int
    candidate: str
    snapshot: AdjacentBookBrowserSnapshot | None
    direction: int
    attempted_paths: frozenset[str]


class NavigationState:
    """Keep request/window indexes paired; service cancellation stays with its owner."""

    def __init__(self) -> None:
        self._sequence = 0
        self.requests_by_window: dict[int, int] = {}
        self.searches: dict[int, AdjacentSearchContext] = {}
        self.side_anchors: dict[int, SideNavigationAnchor] = {}
        self.side_pending: dict[int, PendingSideNavigation] = {}

    def next_identity(self) -> tuple[int, int]:
        self._sequence += 1
        return self._sequence, self._sequence

    def register(self, request_id: int, context: AdjacentSearchContext) -> None:
        window = context.window_ref()
        if window is None:
            return
        previous = self.requests_by_window.get(context.window_id)
        if previous is not None:
            self.discard(previous)
        self.requests_by_window[context.window_id] = request_id
        self.searches[request_id] = context

    def discard(self, request_id: int) -> AdjacentSearchContext | None:
        context = self.searches.pop(request_id, None)
        if context is not None and self.requests_by_window.get(context.window_id) == request_id:
            self.requests_by_window.pop(context.window_id)
        return context

    def cancel(self, window: object) -> tuple[int, AdjacentSearchContext | None] | None:
        request_id = self.requests_by_window.pop(id(window), None)
        if request_id is None:
            return None
        return request_id, self.searches.pop(request_id, None)

    def take_result(self, request_id: int, generation: int) -> AdjacentSearchContext | None:
        context = self.searches.get(request_id)
        if context is None or context.generation != generation:
            return None
        window = context.window_ref()
        if window is None or self.requests_by_window.get(id(window)) != request_id:
            self.discard(request_id)
            return None
        return self.discard(request_id)

    def forget_side_navigation(self, window: object) -> None:
        self.side_anchors.pop(id(window), None)
        self.side_pending.pop(id(window), None)
