from __future__ import annotations

from app.browser_search_history import BrowserSearchHistory


def test_search_history_is_casefolded_mru_with_custom_bounded_limits() -> None:
    history = BrowserSearchHistory((" Foo ", "背景", "foo"), limit=50)
    assert history.entries == ("Foo", "背景")

    assert history.record("foo")
    assert history.entries == ("foo", "背景")
    assert not history.record("  ")

    for limit in (0, 1, 50, 200, 1000):
        candidate = BrowserSearchHistory(limit=limit)
        for index in range(1005):
            candidate.record(f"query-{index:04}")
        assert len(candidate.entries) == min(limit, 1000)

    assert history.set_limit(1)
    assert history.entries == ("foo",)
    assert history.set_limit(0)
    assert history.entries == ()
