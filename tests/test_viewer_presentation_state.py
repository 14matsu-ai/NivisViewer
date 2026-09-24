from __future__ import annotations

import pytest

from app.viewer_presentation_state import (
    PresentationBook,
    PresentationNavigation,
    PresentationPage,
    PresentationSurfaceMode,
    PresentationUnit,
    PresentationValues,
    ViewerPresentationState,
)
from app.viewer_display_unit import ViewerSlotState


def _book(epoch: int = 1) -> PresentationBook:
    return PresentationBook(epoch, 1000 + epoch, f"book-{epoch}")


def _unit(*indexes: int) -> PresentationUnit:
    pages = tuple(
        PresentationPage(index, f"identity-{index}", f"{index}.jpg")
        for index in indexes
    )
    focused = indexes[0]
    return PresentationUnit(
        min(indexes),
        focused,
        f"identity-{focused}",
        pages,
        len(pages) == 1,
    )


def _values(index: int, total: int = 10) -> PresentationValues:
    return PresentationValues(total, index, f"book.zip!/{index}.jpg", 100 + index)


def _request(
    state: ViewerPresentationState,
    index: int,
    *,
    book: PresentationBook | None = None,
    navigation: PresentationNavigation = PresentationNavigation.NORMAL,
):
    return state.request_frame(
        book or _book(),
        _unit(index),
        _values(index),
        ("fit_window", 800, 600),
        1.0,
        navigation,
    )


def _commit(
    state: ViewerPresentationState,
    request,
    serial: int,
):
    return state.commit_frame(
        request.token,
        serial,
        request.unit.page_indexes,
    )


def test_rapid_requests_reject_stale_frame_and_history_uses_last_displayed() -> None:
    state = ViewerPresentationState()
    first = _request(state, 0)
    assert _commit(state, first, 1) is not None
    assert state.committed_frame_serial == 1
    assert state.current_book_epoch == 1
    assert state.displayed_page == 0
    assert state.history_page == 0
    assert state.progress_page == 0

    crossed = _request(state, 1)
    farther = _request(state, 3)
    final = _request(state, 2)
    assert state.request_serial == 4
    assert state.pending_request_serial == 4
    assert state.requested_page == 2
    assert state.direction == -1
    assert state.frame_loading
    assert state.requested_page_indexes == (2,)
    assert state.status_values == _values(0)
    assert state.progress_values == _values(0)
    assert state.commit_frame(crossed.token, 2, (1,)) is None
    assert state.commit_frame(farther.token, 3, (3,)) is None
    assert state.displayed_page_indexes == (0,)
    assert state.slider_page_index == 0

    committed = _commit(state, final, 4)
    assert committed is not None
    assert state.displayed_page_indexes == (2,)
    assert state.committed_frame_serial == 2
    assert not state.frame_loading
    assert [entry.values.page_index for entry in state.back_history] == [0]
    assert all(entry.values.page_index != 1 for entry in state.back_history)


def test_navigation_feedback_is_a_derived_view_not_committed_progress():
    state = ViewerPresentationState()
    assert state.navigation_feedback is None
    first = _request(state, 0)
    assert state.navigation_feedback.page_index == 0
    assert state.status_values is None and state.progress_values is None
    _commit(state, first, 1)
    old = state.displayed
    for page in (1, 3, 2):
        pending = _request(state, page)
        assert state.navigation_feedback.page_index == page
        assert state.slider_page_index == state.progress_page == state.history_page == 0
        assert state.displayed is old
    state.fail_pending("latest abandoned")
    assert state.navigation_feedback.page_index == 0
    assert _commit(state, pending, 2) is None
    _request(state, 4)
    state.supersede_pending()
    assert state.navigation_feedback.page_index == 0

    _request(state, 5)
    state.begin_replacement_open()
    assert state.navigation_feedback.page_index == 0
    state.fail_replacement_open("unavailable book")
    assert state.navigation_feedback.page_index == 0
    replacement = state.request_frame(_book(2), _unit(3), _values(3, 240), (), 1.0)
    assert state.navigation_feedback.total_pages == 10  # Retained old canvas.
    _commit(state, replacement, 3)
    assert state.navigation_feedback.total_pages == 240
    assert state.navigation_feedback.page_index == 3
    state.clear_book()
    assert state.navigation_feedback is None
    state.close()
    assert state.navigation_feedback is None


def test_viewport_fence_preserves_wheel_intent_but_rejects_old_token():
    state = ViewerPresentationState()
    pending = _request(state, 1)

    state.supersede_pending(preserve_intent=True)

    assert state.requested is pending
    assert state.requested_page == 1
    assert state.pending_request_serial == pending.token.request_serial + 1
    assert state.commit_frame(pending.token, 1, (1,)) is None
    assert state.requested is pending

    replacement = _request(state, 1)
    assert replacement.token.request_serial == pending.token.request_serial + 2
    assert state.commit_frame(replacement.token, 2, (1,)) is not None


def test_surface_owner_keeps_loading_and_retained_replacement_explicit() -> None:
    state = ViewerPresentationState()
    assert state.surface.mode is PresentationSurfaceMode.EMPTY

    state.begin_replacement_open()
    loading_revision = state.surface.revision
    assert state.surface.mode is PresentationSurfaceMode.LOADING
    first = _request(state, 0)
    assert state.surface.mode is PresentationSurfaceMode.LOADING
    state.supersede_pending()
    assert state.surface.mode is PresentationSurfaceMode.LOADING
    assert state.surface.revision >= loading_revision

    replacement = _request(state, 0)
    assert _commit(state, replacement, 1) is not None
    assert state.surface.mode is PresentationSurfaceMode.DISPLAYED
    displayed_revision = state.surface.revision

    state.begin_replacement_open()
    assert state.surface.mode is PresentationSurfaceMode.DISPLAYED
    assert state.surface.revision > displayed_revision
    replacement_revision = state.surface.revision
    assert state.fail_replacement_open("broken replacement")
    assert state.surface.mode is PresentationSurfaceMode.DISPLAYED
    assert state.surface.revision > replacement_revision
    assert state.commit_frame(first.token, 2, (0,)) is None

    initial_failure = ViewerPresentationState()
    initial_failure.begin_replacement_open()
    assert initial_failure.fail_replacement_open("broken initial book")
    assert initial_failure.surface.mode is PresentationSurfaceMode.ERROR
    assert initial_failure.surface.message == "broken initial book"


def test_spread_requires_every_logical_slot_to_be_terminal() -> None:
    state = ViewerPresentationState()
    unit = _unit(0, 1)
    request = state.request_frame(
        _book(),
        unit,
        _values(0),
        ("spread", 900, 700),
        1.25,
        PresentationNavigation.NORMAL,
    )
    assert state.transition_slot(
        request.token,
        page_index=0,
        image_id="0.jpg",
        state=ViewerSlotState.READY,
    )
    assert state.commit_frame(request.token, 1, (0,)) is None
    assert state.displayed is None

    committed = state.commit_frame(request.token, 2, (0, 1))
    assert committed is not None
    assert state.displayed_page_indexes == (0, 1)
    assert all(
        slot.state is ViewerSlotState.READY
        for slot in committed.frame.display_tracker.slots
    )
    assert state.commit_frame(request.token, 3, (0, 1)) is None


def test_history_stacks_change_only_when_back_or_forward_frame_commits() -> None:
    state = ViewerPresentationState()
    first = _request(state, 0)
    assert _commit(state, first, 1) is not None
    second = _request(state, 1)
    assert _commit(state, second, 2) is not None

    target = state.history_target(PresentationNavigation.BACK)
    assert target is not None and target.values.page_index == 0
    back = state.request_frame(
        target.book,
        target.unit,
        target.values,
        ("fit_window", 800, 600),
        1.0,
        PresentationNavigation.BACK,
    )
    # Requesting history navigation does not mutate the stacks.
    assert state.history_target(PresentationNavigation.BACK) == target
    assert state.forward_history == ()

    assert _commit(state, back, 3) is not None
    assert state.history_target(PresentationNavigation.BACK) is None
    forward = state.history_target(PresentationNavigation.FORWARD)
    assert forward is not None and forward.values.page_index == 1


def test_error_page_is_a_complete_atomic_frame() -> None:
    state = ViewerPresentationState()
    unit = _unit(4, 5)
    request = state.request_frame(
        _book(),
        unit,
        _values(4),
        ("spread", 640, 480),
        2.0,
        PresentationNavigation.NORMAL,
    )
    committed = state.commit_frame(
        request.token,
        9,
        (4,),
        {5: "broken JPEG"},
    )
    assert committed is not None
    assert committed.committed_frame_serial == 1
    assert committed.frame.has_errors
    assert state.frame_failure is not None
    assert "broken JPEG" in state.frame_failure.message
    assert committed.frame.failed_pages[0].page_index == 5
    assert committed.frame.display_tracker.slots[0].state is ViewerSlotState.READY
    assert committed.frame.display_tracker.slots[1].state is ViewerSlotState.FAILED
    assert state.slider_page_index == 4
    assert state.progress_values == _values(4)


def test_replacement_open_failure_keeps_frame_book_switch_clears_history_and_close_rejects() -> None:
    state = ViewerPresentationState()
    first = _request(state, 0)
    assert _commit(state, first, 1) is not None
    second = _request(state, 1)
    assert _commit(state, second, 2) is not None
    assert state.back_history

    pending_old = _request(state, 2)
    fence_before = state.pending_request_serial
    state.begin_replacement_open()
    assert state.pending_request_serial == fence_before + 1
    assert state.requested is None
    assert state.displayed_page_indexes == (1,)
    assert state.commit_frame(pending_old.token, 3, (2,)) is None
    assert state.fail_replacement_open()
    assert state.displayed_page_indexes == (1,)

    new_book = _book(2)
    replacement = _request(
        state,
        0,
        book=new_book,
        navigation=PresentationNavigation.BOOK_SWITCH,
    )
    switched = _commit(state, replacement, 4)
    assert switched is not None
    assert state.displayed is not None
    assert state.displayed.token.book == new_book
    assert state.back_history == ()
    assert state.forward_history == ()

    late = _request(state, 1, book=new_book)
    state.close()
    assert state.commit_frame(late.token, 5, (1,)) is None
    assert state.displayed is None
    with pytest.raises(RuntimeError, match="closed"):
        _request(state, 2, book=new_book)
