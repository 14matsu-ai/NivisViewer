from __future__ import annotations

from app.browser_thumbnail_scheduler import (
    build_thumbnail_request_plan,
    calculate_grid_visible_range,
)


def test_visible_range_handles_empty_one_and_multiple_columns() -> None:
    assert calculate_grid_visible_range(
        row_count=0,
        viewport_width=800,
        viewport_height=600,
        grid_width=200,
        grid_height=240,
        vertical_offset=0,
    ) is None
    assert calculate_grid_visible_range(
        row_count=100,
        viewport_width=190,
        viewport_height=480,
        grid_width=200,
        grid_height=240,
        vertical_offset=240,
    ) == (1, 3)
    assert calculate_grid_visible_range(
        row_count=100,
        viewport_width=800,
        viewport_height=480,
        grid_width=200,
        grid_height=240,
        vertical_offset=240,
    ) == (4, 15)


def test_visible_range_clamps_at_bottom() -> None:
    assert calculate_grid_visible_range(
        row_count=10,
        viewport_width=400,
        viewport_height=300,
        grid_width=200,
        grid_height=200,
        vertical_offset=20_000,
    ) == (9, 9)


def test_plan_limits_prefetch_for_ten_thousand_items() -> None:
    plan = build_thumbnail_request_plan(
        row_count=10_000,
        first_visible=5000,
        last_visible=5019,
        prefetch_screens=2,
    )

    assert plan.visible_rows == tuple(range(5000, 5020))
    assert plan.directional_rows == tuple(range(5020, 5060))
    assert plan.safety_rows == (4999, 4998, 4997, 4996, 4995)
    assert len(plan.prefetch_rows) == 45
    assert len(plan.requested_rows) == 65
    assert 0 not in plan.requested_rows
    assert 9999 not in plan.requested_rows


def test_fast_scrolling_suppresses_prefetch_but_keeps_selection() -> None:
    plan = build_thumbnail_request_plan(
        row_count=1000,
        first_visible=100,
        last_visible=109,
        selected_rows=(5, 104),
        fast_scrolling=True,
    )

    assert plan.visible_rows == tuple(range(100, 110))
    assert plan.selected_rows == (5,)
    assert plan.prefetch_rows == ()


def test_prefetch_clamps_at_top_and_bottom() -> None:
    top = build_thumbnail_request_plan(
        row_count=20,
        first_visible=0,
        last_visible=4,
        prefetch_screens=2,
    )
    bottom = build_thumbnail_request_plan(
        row_count=20,
        first_visible=15,
        last_visible=19,
        prefetch_screens=2,
        scroll_direction=-1,
    )

    assert top.requested_rows == tuple(range(15))
    assert bottom.directional_rows == tuple(range(14, 4, -1))
    assert bottom.safety_rows == ()
    assert set(bottom.requested_rows) == set(range(5, 20))


def test_direction_change_recenters_one_viewport_and_quarter_screen_safety() -> None:
    down = build_thumbnail_request_plan(
        row_count=1000,
        first_visible=100,
        last_visible=119,
        scroll_direction=1,
    )
    up = build_thumbnail_request_plan(
        row_count=1000,
        first_visible=100,
        last_visible=119,
        scroll_direction=-1,
    )

    assert down.directional_rows == tuple(range(120, 140))
    assert down.safety_rows == (99, 98, 97, 96, 95)
    assert up.directional_rows == tuple(range(99, 79, -1))
    assert up.safety_rows == (120, 121, 122, 123, 124)
    assert len(down.prefetch_rows) == len(up.prefetch_rows) == 25


def test_speculative_range_is_bounded_by_visible_count_not_total_count() -> None:
    small = build_thumbnail_request_plan(
        row_count=500,
        first_visible=200,
        last_visible=219,
    )
    huge = build_thumbnail_request_plan(
        row_count=1_000_000,
        first_visible=200,
        last_visible=219,
    )

    assert len(small.prefetch_rows) == len(huge.prefetch_rows) == 25
    assert max(huge.requested_rows) == 239
    assert min(huge.requested_rows) == 195
