from __future__ import annotations

import pytest

from app.viewer_widget import calculate_spread_layout


def center_gap(layout) -> int:
    return layout.rects[1].left() - layout.rects[0].right() - 1


def test_normal_spread_uses_configured_gap() -> None:
    layout = calculate_spread_layout(
        [(800, 1200), (800, 1200)],
        (1200, 800),
        gap=24,
    )

    assert layout.effective_gap == 24
    assert center_gap(layout) == 24


@pytest.mark.parametrize("fit_mode", ["fit_window", "actual_size", "manual_zoom"])
def test_joined_spread_has_no_center_gap_and_shared_scale(fit_mode: str) -> None:
    layout = calculate_spread_layout(
        [(800, 1200), (600, 900)],
        (1400, 900),
        fit_mode=fit_mode,
        manual_zoom=1.35,
        gap=42,
        join_spread_pages=True,
    )

    assert layout.effective_gap == 0
    assert center_gap(layout) == 0
    first_scale = layout.rects[0].width() / 800
    second_scale = layout.rects[1].width() / 600
    assert first_scale == pytest.approx(second_scale, abs=0.002)


def test_joined_spread_centers_different_heights_and_preserves_input_order() -> None:
    rtl_order = [(500, 1000), (700, 700)]
    layout = calculate_spread_layout(
        rtl_order,
        (1200, 900),
        join_spread_pages=True,
    )

    assert center_gap(layout) == 0
    assert layout.rects[0].width() < layout.rects[1].width()
    assert layout.rects[1].top() > layout.rects[0].top()
    assert layout.rects[0].center().y() == pytest.approx(
        layout.rects[1].center().y(),
        abs=1,
    )


def test_join_setting_does_not_change_single_or_split_single_page() -> None:
    single = calculate_spread_layout(
        [(800, 1200)],
        (1000, 800),
        gap=35,
        join_spread_pages=True,
        spread_is_single=True,
    )
    split_single = calculate_spread_layout(
        [(600, 900), (600, 900)],
        (1200, 900),
        gap=35,
        join_spread_pages=True,
        spread_is_single=True,
    )

    assert single.effective_gap == 0
    assert split_single.effective_gap == 35
    assert center_gap(split_single) == 35
