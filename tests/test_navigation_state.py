from types import SimpleNamespace
from weakref import ref

from app.navigation_state import AdjacentSearchContext, BookOpenFence, NavigationState


class Window:
    pass


def register(state, window):
    request_id, generation = state.next_identity()
    context = AdjacentSearchContext(ref(window), generation, 1, 'book', 'book')
    state.register(request_id, context)
    return request_id, context


def test_wrong_generation_does_not_consume_live_search():
    state = NavigationState()
    window = Window()
    request_id, context = register(state, window)
    assert state.take_result(request_id, context.generation - 1) is None
    assert state.requests_by_window[id(window)] == request_id
    assert state.take_result(request_id, context.generation) is context
    assert not state.searches and not state.requests_by_window


def test_replacement_and_late_result_cannot_remove_new_search():
    state = NavigationState()
    window = Window()
    old_id, old = register(state, window)
    new_id, new = register(state, window)
    assert state.take_result(old_id, old.generation) is None
    assert state.requests_by_window[id(window)] == new_id
    assert state.cancel(window) == (new_id, new)
    assert state.take_result(new_id, new.generation) is None
    assert not state.searches and not state.requests_by_window


def test_dead_window_does_not_leave_request_index():
    state = NavigationState()
    window = Window()
    request_id, context = register(state, window)
    del window
    assert context.window_ref() is None
    assert state.take_result(request_id, context.generation) is None
    assert not state.searches and not state.requests_by_window


def test_book_open_fence_includes_source_and_pending_open():
    session = SimpleNamespace(generation=3, source=object(), _open_generation=7)
    fence = BookOpenFence.capture(session)
    assert fence.matches(session)
    session._open_generation += 1
    assert not fence.matches(session)
    session._open_generation -= 1
    session.source = object()
    assert not fence.matches(session)
