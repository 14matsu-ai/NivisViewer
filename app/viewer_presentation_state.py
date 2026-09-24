from __future__ import annotations

from .i18n import tr


from dataclasses import dataclass, replace
from enum import Enum
from math import isfinite
from typing import Hashable, Iterable, Mapping

from .viewer_display_unit import (
    ViewerDisplayUnit,
    ViewerSlotState,
)


class PresentationNavigation(str, Enum):
    REFRESH = "refresh"
    NORMAL = "normal"
    BACK = "back"
    FORWARD = "forward"
    BOOK_SWITCH = "book_switch"


class PresentationSurfaceMode(str, Enum):
    """Canvas state projected from the presentation owner.

    ``DISPLAYED`` remains authoritative while a replacement book is opening
    or another page is loading.  The Widget must never infer ``EMPTY`` merely
    because its image list happens to be empty between an open request and the
    first complete frame.
    """

    EMPTY = "empty"
    LOADING = "loading"
    DISPLAYED = "displayed"
    ERROR = "error"


@dataclass(frozen=True)
class PresentationSurface:
    mode: PresentationSurfaceMode
    revision: int
    message: str | None = None


@dataclass(frozen=True)
class PresentationPage:
    index: int
    page_identity: str
    image_id: str

    def __post_init__(self) -> None:
        index = int(self.index)
        page_identity = str(self.page_identity)
        image_id = str(self.image_id)
        if index < 0:
            raise ValueError("page index must be non-negative")
        if not page_identity:
            raise ValueError("page_identity must not be empty")
        if not image_id:
            raise ValueError("image_id must not be empty")
        object.__setattr__(self, "index", index)
        object.__setattr__(self, "page_identity", page_identity)
        object.__setattr__(self, "image_id", image_id)


@dataclass(frozen=True)
class PresentationUnitIdentity:
    start_index: int
    focused_index: int
    focused_page_identity: str
    pages: tuple[PresentationPage, ...]
    is_single: bool


@dataclass(frozen=True)
class PresentationUnit:
    start_index: int
    focused_index: int
    focused_page_identity: str
    pages: tuple[PresentationPage, ...]
    is_single: bool

    def __post_init__(self) -> None:
        start_index = int(self.start_index)
        focused_index = int(self.focused_index)
        focused_page_identity = str(self.focused_page_identity)
        pages = tuple(self.pages)
        is_single = bool(self.is_single)
        if start_index < 0 or focused_index < 0:
            raise ValueError("unit indexes must be non-negative")
        if not focused_page_identity:
            raise ValueError("focused_page_identity must not be empty")
        if not pages or len(pages) > 2:
            raise ValueError("a presentation unit must contain one or two pages")
        if any(not isinstance(page, PresentationPage) for page in pages):
            raise TypeError("pages must contain PresentationPage values")
        if is_single != (len(pages) == 1):
            raise ValueError("is_single must match the logical page count")
        if len({page.index for page in pages}) != len(pages):
            raise ValueError("logical page indexes must be unique")
        if not any(
            page.index == focused_index
            and page.page_identity == focused_page_identity
            for page in pages
        ):
            raise ValueError("the focused page must belong to the unit")
        object.__setattr__(self, "start_index", start_index)
        object.__setattr__(self, "focused_index", focused_index)
        object.__setattr__(
            self,
            "focused_page_identity",
            focused_page_identity,
        )
        object.__setattr__(self, "pages", pages)
        object.__setattr__(self, "is_single", is_single)

    @property
    def identity(self) -> PresentationUnitIdentity:
        return PresentationUnitIdentity(
            self.start_index,
            self.focused_index,
            self.focused_page_identity,
            self.pages,
            self.is_single,
        )

    @property
    def page_indexes(self) -> tuple[int, ...]:
        return tuple(page.index for page in self.pages)


@dataclass(frozen=True)
class PresentationBook:
    epoch: int
    source_identity: int
    book_key: str

    def __post_init__(self) -> None:
        epoch = int(self.epoch)
        source_identity = int(self.source_identity)
        book_key = str(self.book_key)
        if epoch < 0:
            raise ValueError("book epoch must be non-negative")
        if not book_key:
            raise ValueError("book_key must not be empty")
        object.__setattr__(self, "epoch", epoch)
        object.__setattr__(self, "source_identity", source_identity)
        object.__setattr__(self, "book_key", book_key)


@dataclass(frozen=True)
class PresentationValues:
    total_pages: int
    page_index: int
    path: str
    file_size: int | None

    def __post_init__(self) -> None:
        total_pages = int(self.total_pages)
        page_index = int(self.page_index)
        file_size = self.file_size
        if total_pages <= 0:
            raise ValueError("total_pages must be positive")
        if page_index < 0 or page_index >= total_pages:
            raise ValueError("page_index must be inside the book")
        if file_size is not None:
            file_size = max(0, int(file_size))
        object.__setattr__(self, "total_pages", total_pages)
        object.__setattr__(self, "page_index", page_index)
        object.__setattr__(self, "path", str(self.path))
        object.__setattr__(self, "file_size", file_size)


@dataclass(frozen=True)
class PresentationNavigationFeedback:
    """Page-only controls, deliberately separate from committed image details."""

    total_pages: int
    page_index: int


@dataclass(frozen=True)
class PresentationFrameToken:
    book: PresentationBook
    request_serial: int
    layout_signature: Hashable
    dpr_milli: int
    unit_identity: PresentationUnitIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.book, PresentationBook):
            raise TypeError("book must be PresentationBook")
        if not isinstance(self.unit_identity, PresentationUnitIdentity):
            raise TypeError("unit_identity must be PresentationUnitIdentity")
        request_serial = int(self.request_serial)
        dpr_milli = int(self.dpr_milli)
        if request_serial <= 0:
            raise ValueError("request_serial must be positive")
        if dpr_milli <= 0:
            raise ValueError("dpr_milli must be positive")
        try:
            hash(self.layout_signature)
        except TypeError as exc:
            raise TypeError("layout_signature must be hashable") from exc
        object.__setattr__(self, "request_serial", request_serial)
        object.__setattr__(self, "dpr_milli", dpr_milli)


@dataclass(frozen=True)
class PresentationRequest:
    token: PresentationFrameToken
    unit: PresentationUnit
    values: PresentationValues
    progress_values: PresentationValues
    direction: int
    navigation: PresentationNavigation
    display_tracker: ViewerDisplayUnit

    def __post_init__(self) -> None:
        direction = int(self.direction)
        if direction not in {-1, 0, 1}:
            raise ValueError("direction must be -1, 0, or 1")
        object.__setattr__(self, "direction", direction)


@dataclass(frozen=True)
class PresentationFailedPage:
    page_index: int
    message: str


@dataclass(frozen=True)
class PresentationFrame:
    request: PresentationRequest
    completed_page_indexes: tuple[int, ...]
    failed_pages: tuple[PresentationFailedPage, ...]
    display_tracker: ViewerDisplayUnit

    @property
    def token(self) -> PresentationFrameToken:
        return self.request.token

    @property
    def unit(self) -> PresentationUnit:
        return self.request.unit

    @property
    def values(self) -> PresentationValues:
        return self.request.values

    @property
    def has_errors(self) -> bool:
        return bool(self.failed_pages)


@dataclass(frozen=True)
class PresentationHistoryEntry:
    book: PresentationBook
    unit: PresentationUnit
    values: PresentationValues


@dataclass(frozen=True)
class PresentationCommit:
    frame: PresentationFrame
    previous_frame: PresentationFrame | None
    widget_frame_serial: int
    committed_frame_serial: int
    slider_page_index: int
    status_values: PresentationValues
    progress_values: PresentationValues
    back_history: tuple[PresentationHistoryEntry, ...]
    forward_history: tuple[PresentationHistoryEntry, ...]


@dataclass(frozen=True)
class PresentationFailure:
    token: PresentationFrameToken
    message: str


@dataclass(frozen=True)
class _CommittedSnapshot:
    displayed: PresentationFrame | None = None
    slider_page_index: int | None = None
    status_values: PresentationValues | None = None
    progress_values: PresentationValues | None = None
    widget_frame_serial: int = 0
    committed_frame_serial: int = 0
    frame_failure: PresentationFailure | None = None
    back_history: tuple[PresentationHistoryEntry, ...] = ()
    forward_history: tuple[PresentationHistoryEntry, ...] = ()


class ViewerPresentationState:
    """Single owner for requested, displayed, UI and history state.

    The class deliberately owns no Qt image or widget.  Callers may keep the
    preceding canvas visible while ``requested`` changes; only a complete,
    exact-token frame replaces the committed snapshot.
    """

    _HISTORY_LIMIT = 100

    def __init__(self) -> None:
        self._request_serial = 0
        self._requested: PresentationRequest | None = None
        self._committed = _CommittedSnapshot()
        self._direction = 0
        self._replacement_open_pending = False
        self._last_failure: PresentationFailure | None = None
        self._surface = PresentationSurface(
            PresentationSurfaceMode.EMPTY,
            0,
        )
        self._closed = False

    @property
    def request_serial(self) -> int:
        return self._request_serial

    @property
    def pending_request_serial(self) -> int:
        """Latest request/fence serial, including replacement-open fences."""

        return self._request_serial

    @property
    def committed_frame_serial(self) -> int:
        return self._committed.committed_frame_serial

    @property
    def current_book_epoch(self) -> int | None:
        displayed = self._committed.displayed
        return displayed.token.book.epoch if displayed is not None else None

    @property
    def requested(self) -> PresentationRequest | None:
        return self._requested

    @property
    def displayed(self) -> PresentationFrame | None:
        return self._committed.displayed

    @property
    def displayed_values(self) -> PresentationValues | None:
        return self._committed.status_values

    @property
    def requested_page(self) -> int | None:
        return (
            self._requested.values.page_index
            if self._requested is not None
            else None
        )

    @property
    def displayed_page(self) -> int | None:
        displayed = self._committed.displayed
        return displayed.values.page_index if displayed is not None else None

    @property
    def history_page(self) -> int | None:
        """Page from the last committed frame, never a rapid pending page."""

        return self.displayed_page

    @property
    def progress_page(self) -> int | None:
        values = self._committed.progress_values
        return values.page_index if values is not None else None

    @property
    def frame_loading(self) -> bool:
        return bool(
            self._surface.mode is PresentationSurfaceMode.LOADING
            or self._replacement_open_pending
            or (
                self._requested is not None
                and (
                    self._committed.displayed is None
                    or self._committed.displayed.token != self._requested.token
                )
            )
        )

    @property
    def surface(self) -> PresentationSurface:
        """Latest canvas disposition, including its stale-update fence."""

        return self._surface

    @property
    def frame_failure(self) -> PresentationFailure | None:
        return self._last_failure or self._committed.frame_failure

    @property
    def slider_page_index(self) -> int | None:
        return self._committed.slider_page_index

    @property
    def status_values(self) -> PresentationValues | None:
        return self._committed.status_values

    @property
    def navigation_feedback(self) -> PresentationNavigationFeedback | None:
        """Latest accepted same-book destination, without advancing read state.

        Request creation precedes runtime admission/staging. Abandonment fences
        clear requested, so the retained presentation becomes the fallback.
        During replacement keep the old displayed book's controls until its
        replacement commits; on initial open there is no old book to retain.
        """
        requested = self._requested
        displayed = self._committed.displayed
        values = self._committed.status_values
        if requested is not None and (
            displayed is None or requested.token.book == displayed.token.book
        ):
            values = requested.values
        if values is None:
            return None
        return PresentationNavigationFeedback(values.total_pages, values.page_index)

    @property
    def progress_values(self) -> PresentationValues | None:
        return self._committed.progress_values

    @property
    def widget_frame_serial(self) -> int:
        return self._committed.widget_frame_serial

    @property
    def direction(self) -> int:
        return self._direction

    @property
    def display_tracker(self) -> ViewerDisplayUnit | None:
        return (
            self._requested.display_tracker
            if self._requested is not None
            else None
        )

    @property
    def requested_page_indexes(self) -> tuple[int, ...]:
        return (
            self._requested.unit.page_indexes
            if self._requested is not None
            else ()
        )

    @property
    def displayed_page_indexes(self) -> tuple[int, ...]:
        return (
            self._committed.displayed.unit.page_indexes
            if self._committed.displayed is not None
            else ()
        )

    @property
    def last_failure(self) -> PresentationFailure | None:
        return self._last_failure

    @property
    def replacement_open_pending(self) -> bool:
        return self._replacement_open_pending

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def back_history(self) -> tuple[PresentationHistoryEntry, ...]:
        return self._committed.back_history

    @property
    def forward_history(self) -> tuple[PresentationHistoryEntry, ...]:
        return self._committed.forward_history

    def request_frame(
        self,
        book: PresentationBook,
        unit: PresentationUnit,
        values: PresentationValues,
        layout_signature: Hashable,
        dpr: float,
        navigation: PresentationNavigation = PresentationNavigation.NORMAL,
        progress_values: PresentationValues | None = None,
    ) -> PresentationRequest:
        if self._closed:
            raise RuntimeError("presentation state is closed")
        if not isinstance(book, PresentationBook):
            raise TypeError("book must be PresentationBook")
        if not isinstance(unit, PresentationUnit):
            raise TypeError("unit must be PresentationUnit")
        if not isinstance(values, PresentationValues):
            raise TypeError("values must be PresentationValues")
        if values.page_index != unit.focused_index:
            raise ValueError("presentation values must describe the focused page")
        normalized_progress = progress_values or values
        if not isinstance(normalized_progress, PresentationValues):
            raise TypeError("progress_values must be PresentationValues")
        try:
            normalized_navigation = PresentationNavigation(navigation)
        except ValueError as exc:
            raise ValueError("unsupported presentation navigation") from exc
        displayed = self._committed.displayed
        if displayed is not None and displayed.token.book != book:
            normalized_navigation = PresentationNavigation.BOOK_SWITCH
        self._validate_history_request(
            normalized_navigation,
            book,
            unit,
            values,
        )
        normalized_dpr = float(dpr)
        if not isfinite(normalized_dpr) or normalized_dpr <= 0:
            raise ValueError("dpr must be positive and finite")
        try:
            hash(layout_signature)
        except TypeError as exc:
            raise TypeError("layout_signature must be hashable") from exc

        self._request_serial += 1
        direction = self._direction_for(book, unit, normalized_navigation)
        tracker = ViewerDisplayUnit.create(
            request_id=self._request_serial,
            generation=book.epoch,
            focused_page_identity=unit.focused_page_identity,
            pages=(
                (page.index, page.page_identity, page.image_id)
                for page in unit.pages
            ),
        )
        token = PresentationFrameToken(
            book,
            self._request_serial,
            layout_signature,
            max(1, round(normalized_dpr * 1000)),
            unit.identity,
        )
        request = PresentationRequest(
            token,
            unit,
            values,
            normalized_progress,
            direction,
            normalized_navigation,
            tracker,
        )
        self._requested = request
        self._direction = direction
        self._replacement_open_pending = False
        self._last_failure = None
        self._set_surface(
            PresentationSurfaceMode.DISPLAYED
            if self._committed.displayed is not None
            else PresentationSurfaceMode.LOADING,
            force_revision=True,
        )
        return request

    def transition_slot(
        self,
        token: PresentationFrameToken,
        *,
        page_index: int,
        image_id: str,
        state: ViewerSlotState,
        error: str | None = None,
    ) -> bool:
        request = self._requested
        if (
            self._closed
            or request is None
            or request.token != token
            or request.token.request_serial != self._request_serial
        ):
            return False
        normalized_state = ViewerSlotState(state)
        previous = request.display_tracker
        updated = previous.transition(
            page_index=int(page_index),
            image_id=str(image_id),
            generation=previous.generation,
            state=normalized_state,
            error=error,
        )
        if updated is previous:
            return False
        self._requested = replace(request, display_tracker=updated)
        return True

    def commit_frame(
        self,
        token: PresentationFrameToken,
        widget_serial: int,
        completed_page_indexes: Iterable[int],
        failed_pages: Mapping[int, str] | Iterable[tuple[int, str]] = (),
    ) -> PresentationCommit | None:
        request = self._requested
        if (
            self._closed
            or request is None
            or request.token != token
            or request.token.request_serial != self._request_serial
            or token.book != request.token.book
            or token.layout_signature != request.token.layout_signature
            or token.dpr_milli != request.token.dpr_milli
            or token.unit_identity != request.unit.identity
            or (
                self._committed.displayed is not None
                and self._committed.displayed.token == token
            )
        ):
            return None
        normalized_widget_serial = int(widget_serial)
        if normalized_widget_serial <= 0:
            raise ValueError("widget_serial must be positive")

        completed = {int(index) for index in completed_page_indexes}
        failed_map = self._normalize_failed_pages(failed_pages)
        failed_indexes = set(failed_map)
        expected = set(request.unit.page_indexes)
        if (
            completed & failed_indexes
            or completed | failed_indexes != expected
            or not completed.issubset(expected)
            or not failed_indexes.issubset(expected)
        ):
            return None

        tracker = request.display_tracker
        for page in request.unit.pages:
            error = failed_map.get(page.index)
            tracker = tracker.transition(
                page_index=page.index,
                image_id=page.image_id,
                generation=tracker.generation,
                state=(
                    ViewerSlotState.FAILED
                    if error is not None
                    else ViewerSlotState.READY
                ),
                error=error,
            )
        terminal_states = {
            ViewerSlotState.READY,
            ViewerSlotState.FAILED,
        }
        if (
            len(tracker.slots) != len(request.unit.pages)
            or any(slot.state not in terminal_states for slot in tracker.slots)
        ):
            return None

        failed = tuple(
            PresentationFailedPage(page.index, failed_map[page.index])
            for page in request.unit.pages
            if page.index in failed_map
        )
        normalized_completed = tuple(
            page.index for page in request.unit.pages if page.index in completed
        )
        committed_request = replace(request, display_tracker=tracker)
        frame = PresentationFrame(
            committed_request,
            normalized_completed,
            failed,
            tracker,
        )
        previous = self._committed.displayed
        back, forward = self._updated_history(previous, frame)
        committed_frame_serial = self._committed.committed_frame_serial + 1
        frame_failure = (
            PresentationFailure(
                token,
                "; ".join(failure.message for failure in failed),
            )
            if failed
            else None
        )
        # Committed image details/history/progress change through one immutable
        # assignment. Page-only navigation feedback is a separate derived view.
        committed = _CommittedSnapshot(
            displayed=frame,
            slider_page_index=frame.values.page_index,
            status_values=frame.values,
            progress_values=frame.request.progress_values,
            widget_frame_serial=normalized_widget_serial,
            committed_frame_serial=committed_frame_serial,
            frame_failure=frame_failure,
            back_history=back,
            forward_history=forward,
        )
        self._requested = committed_request
        self._committed = committed
        self._last_failure = None
        self._set_surface(
            PresentationSurfaceMode.DISPLAYED,
            force_revision=True,
        )
        return PresentationCommit(
            frame,
            previous,
            normalized_widget_serial,
            committed_frame_serial,
            frame.values.page_index,
            frame.values,
            frame.request.progress_values,
            back,
            forward,
        )

    def begin_replacement_open(self) -> None:
        if self._closed:
            return
        # This fence is observable before a replacement source is ready.  A
        # Window precheck using the previous request serial therefore rejects
        # a frame even before commit_frame() examines ``requested``.
        self.supersede_pending()
        self._replacement_open_pending = True
        self._set_surface(
            PresentationSurfaceMode.DISPLAYED
            if self._committed.displayed is not None
            else PresentationSurfaceMode.LOADING,
            force_revision=True,
        )

    def supersede_pending(self, *, preserve_intent: bool = False) -> None:
        """Fence a request while retaining the last committed presentation.

        A viewport/layout fence can arrive while a wheel destination is still
        being rendered. Preserve that logical destination until the owner
        rebuilds the request for the new render specification.
        """

        if self._closed:
            return
        self._request_serial += 1
        if not preserve_intent:
            self._requested = None
        self._last_failure = None
        # A viewport/layout fence must not turn an initial in-flight open back
        # into the idle prompt.  With a committed frame, that frame remains
        # the canvas owner until an exact replacement commits.
        self._set_surface(
            PresentationSurfaceMode.DISPLAYED
            if self._committed.displayed is not None
            else PresentationSurfaceMode.LOADING,
            force_revision=True,
        )

    def fail_replacement_open(self, message: str | None = None) -> bool:
        if self._closed or not self._replacement_open_pending:
            return False
        self._replacement_open_pending = False
        if self._committed.displayed is not None:
            self._set_surface(
                PresentationSurfaceMode.DISPLAYED,
                force_revision=True,
            )
        elif message:
            self._set_surface(
                PresentationSurfaceMode.ERROR,
                str(message),
                force_revision=True,
            )
        else:
            self._set_surface(
                PresentationSurfaceMode.EMPTY,
                force_revision=True,
            )
        return True

    def fail_pending(self, message: str) -> PresentationFailure | None:
        request = self._requested
        if self._closed or request is None:
            return None
        failure = PresentationFailure(
            request.token,
            str(message) or "Viewer frame request failed.",
        )
        self._requested = None
        self._last_failure = failure
        self._set_surface(
            PresentationSurfaceMode.DISPLAYED
            if self._committed.displayed is not None
            else PresentationSurfaceMode.ERROR,
            None if self._committed.displayed is not None else failure.message,
            force_revision=True,
        )
        return failure

    def history_target(
        self,
        navigation: PresentationNavigation,
    ) -> PresentationHistoryEntry | None:
        normalized = PresentationNavigation(navigation)
        if normalized is PresentationNavigation.BACK:
            history = self._committed.back_history
        elif normalized is PresentationNavigation.FORWARD:
            history = self._committed.forward_history
        else:
            raise ValueError("history_target requires BACK or FORWARD")
        return history[-1] if history else None

    def clear_history(self) -> None:
        self._committed = replace(
            self._committed,
            back_history=(),
            forward_history=(),
        )

    def clear_book(self) -> None:
        """Fence callbacks and reset the active presentation for an empty book."""

        if self._closed:
            return
        self._request_serial += 1
        committed_serial = self._committed.committed_frame_serial
        self._requested = None
        self._committed = _CommittedSnapshot(
            committed_frame_serial=committed_serial,
        )
        self._direction = 0
        self._replacement_open_pending = False
        self._last_failure = None
        self._set_surface(
            PresentationSurfaceMode.EMPTY,
            force_revision=True,
        )

    def close(self) -> None:
        if self._closed:
            return
        self.clear_book()
        self._closed = True

    def _set_surface(
        self,
        mode: PresentationSurfaceMode,
        message: str | None = None,
        *,
        force_revision: bool = False,
    ) -> None:
        normalized_mode = PresentationSurfaceMode(mode)
        normalized_message = str(message) if message else None
        if not force_revision and (
            self._surface.mode is normalized_mode
            and self._surface.message == normalized_message
        ):
            return
        self._surface = PresentationSurface(
            normalized_mode,
            self._surface.revision + 1,
            normalized_message,
        )

    def _direction_for(
        self,
        book: PresentationBook,
        unit: PresentationUnit,
        navigation: PresentationNavigation,
    ) -> int:
        if navigation is PresentationNavigation.BOOK_SWITCH:
            return 0
        previous_index: int | None = None
        if self._requested is not None and self._requested.token.book == book:
            previous_index = self._requested.unit.focused_index
        elif (
            self._committed.displayed is not None
            and self._committed.displayed.token.book == book
        ):
            previous_index = self._committed.displayed.unit.focused_index
        if previous_index is None:
            return 0
        delta = unit.focused_index - previous_index
        if delta:
            return 1 if delta > 0 else -1
        return self._direction

    def _validate_history_request(
        self,
        navigation: PresentationNavigation,
        book: PresentationBook,
        unit: PresentationUnit,
        values: PresentationValues,
    ) -> None:
        if navigation not in {
            PresentationNavigation.BACK,
            PresentationNavigation.FORWARD,
        }:
            return
        target = self.history_target(navigation)
        if (
            target is None
            or target.book != book
            or target.unit.identity != unit.identity
            or target.values.page_index != values.page_index
        ):
            raise ValueError("history request does not match the current target")

    def _updated_history(
        self,
        previous: PresentationFrame | None,
        frame: PresentationFrame,
    ) -> tuple[
        tuple[PresentationHistoryEntry, ...],
        tuple[PresentationHistoryEntry, ...],
    ]:
        back = list(self._committed.back_history)
        forward = list(self._committed.forward_history)
        navigation = frame.request.navigation
        book_changed = (
            previous is not None and previous.token.book != frame.token.book
        )
        if navigation is PresentationNavigation.BOOK_SWITCH or book_changed:
            return (), ()
        if previous is None:
            return tuple(back), tuple(forward)

        previous_entry = self._history_entry(previous)
        if navigation is PresentationNavigation.NORMAL:
            if previous.unit.identity != frame.unit.identity:
                self._append_history(back, previous_entry)
                forward.clear()
        elif navigation is PresentationNavigation.BACK:
            target = back.pop()
            if target.unit.identity != frame.unit.identity:
                raise RuntimeError("back history changed before frame commit")
            self._append_history(forward, previous_entry)
        elif navigation is PresentationNavigation.FORWARD:
            target = forward.pop()
            if target.unit.identity != frame.unit.identity:
                raise RuntimeError("forward history changed before frame commit")
            self._append_history(back, previous_entry)
        return tuple(back), tuple(forward)

    @staticmethod
    def _history_entry(frame: PresentationFrame) -> PresentationHistoryEntry:
        return PresentationHistoryEntry(
            frame.token.book,
            frame.unit,
            frame.values,
        )

    @classmethod
    def _append_history(
        cls,
        history: list[PresentationHistoryEntry],
        entry: PresentationHistoryEntry,
    ) -> None:
        if history and history[-1].unit.identity == entry.unit.identity:
            history[-1] = entry
        else:
            history.append(entry)
        del history[:-cls._HISTORY_LIMIT]

    @staticmethod
    def _normalize_failed_pages(
        failed_pages: Mapping[int, str] | Iterable[tuple[int, str]],
    ) -> dict[int, str]:
        raw = failed_pages.items() if isinstance(failed_pages, Mapping) else failed_pages
        normalized: dict[int, str] = {}
        for page_index, message in raw:
            index = int(page_index)
            if index in normalized:
                raise ValueError("failed page indexes must be unique")
            normalized[index] = str(message) or tr('画像を表示できません。')
        return normalized


# A short compatibility name for callers that already use ``Navigation``.
Navigation = PresentationNavigation
