from __future__ import annotations

from app.browser_navigation import BrowserLocation, BrowserNavigationHistory


def location(name: str) -> BrowserLocation:
    return BrowserLocation(path=name)


def test_back_and_forward_follow_visit_order() -> None:
    history = BrowserNavigationHistory()
    for name in ("A", "B", "C"):
        history.visit(location(name))

    assert history.go_back() == location("B")
    assert history.go_back() == location("A")
    assert history.go_back() is None
    assert history.go_forward() == location("B")


def test_new_visit_after_back_discards_forward_branch() -> None:
    history = BrowserNavigationHistory()
    for name in ("A", "B", "C"):
        history.visit(location(name))
    assert history.go_back() == location("B")

    history.visit(location("D"))

    assert history.current() == location("D")
    assert not history.can_go_forward()
    assert history.go_back() == location("B")


def test_consecutive_duplicate_and_windows_case_variant_are_not_added() -> None:
    history = BrowserNavigationHistory()
    history.visit(location(r"C:\漫画\本"))
    history.visit(location("c:/漫画/本/"))

    assert len(history) == 1
    assert len(history.recent_unique()) == 1


def test_history_limit_drops_oldest_entries() -> None:
    history = BrowserNavigationHistory(max_entries=3)
    for name in ("A", "B", "C", "D"):
        history.visit(location(name))

    assert len(history) == 3
    assert history.go_back() == location("C")
    assert history.go_back() == location("B")
    assert history.go_back() is None


def test_current_view_state_is_returned_by_navigation() -> None:
    history = BrowserNavigationHistory()
    history.visit(location("A"))
    history.update_current_view_state(
        selected_path="A/item.jpg",
        vertical_scroll=42,
        horizontal_scroll=7,
    )
    history.visit(location("B"))

    restored = history.go_back()

    assert restored == BrowserLocation(
        path="A",
        selected_path="A/item.jpg",
        vertical_scroll=42,
        horizontal_scroll=7,
    )


def test_empty_history_operations_are_safe() -> None:
    history = BrowserNavigationHistory()

    history.update_current_view_state(
        selected_path=None,
        vertical_scroll=10,
        horizontal_scroll=10,
    )

    assert history.current() is None
    assert history.go_back() is None
    assert history.go_forward() is None
    assert not history.can_go_back()
    assert not history.can_go_forward()


def test_direct_jump_and_recent_locations_reuse_the_timeline() -> None:
    history = BrowserNavigationHistory()
    for name in ("A", "B", "A", "C"):
        history.visit(location(name))

    assert history.current_index == 3
    assert history.go_to(1) == location("B")
    assert history.current_index == 1
    assert history.go_to(99) is None
    assert history.current_index == 1
    assert history.entries == (
        location("A"),
        location("B"),
        location("A"),
        location("C"),
    )
    assert [item.path for _index, item in history.recent_unique()] == [
        "C",
        "A",
        "B",
    ]


def test_recent_limit_only_trims_mru_and_preserves_navigation_timeline() -> None:
    history = BrowserNavigationHistory(max_entries=300, recent_limit=200)
    for index in range(60):
        history.visit(location(f"place-{index:03}"))

    assert len(history) == 60
    assert len(history.recent_unique()) == 60
    assert history.set_recent_limit(10)
    assert len(history.recent_unique()) == 10
    assert len(history) == 60
    assert history.go_back() == location("place-058")
    assert history.mark_recent(history.current()) is None
    assert history.recent_unique()[0][1] == location("place-058")
    assert history.remove_recent("place-058")
    assert history.recent_unique()[0][1] == location("place-059")
    assert not history.remove_recent("missing")


def test_relocate_path_updates_folder_and_selected_path() -> None:
    history = BrowserNavigationHistory()
    history.visit(
        BrowserLocation(
            path=r"C:\Books\Old",
            selected_path=r"C:\Books\Old\book.cbz",
            vertical_scroll=20,
        )
    )

    assert history.relocate_path(r"C:\Books\Old", r"C:\Books\New")

    assert history.current() == BrowserLocation(
        path=r"C:\Books\New",
        selected_path=r"C:\Books\New\book.cbz",
        vertical_scroll=20,
    )


def test_relocate_tree_updates_prefix_and_does_not_create_duplicate() -> None:
    history = BrowserNavigationHistory()
    history.visit(location(r"C:\Books\Old"))
    history.visit(location(r"C:\Books\Old\Child"))
    history.visit(location(r"C:\Other"))
    history.go_back()

    assert history.relocate_tree(r"C:\Books\Old", r"D:\Library\New")

    assert history.current() == location(r"D:\Library\New\Child")
    assert len(history) == 3
