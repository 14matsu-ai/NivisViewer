from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication

from app.config_manager import ConfigManager
from app.page_model import DisplaySpread, PageSlot
from app.viewer_render import (
    RESAMPLING_MODE_LABELS,
    ViewerRenderKey,
    ViewerRenderResult,
    pillow_resampling_for,
    render_qimage,
)
from app.viewer_widget import ViewerWidget
from app.viewer_window import ViewerWindow


def _image(width: int = 240, height: int = 160) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.white)
    return image


def _set_single_page(
    widget: ViewerWidget,
    *,
    image: QImage | None = None,
    rendered_size: tuple[int, int] | None = None,
) -> None:
    source = image or _image()
    widget.set_pages(
        DisplaySpread(0, (PageSlot("page", 0),), True),
        [
            ViewerWidget.from_qimage(
                0,
                "page",
                source,
                (source.width(), source.height()),
                rendered_size=rendered_size,
            )
        ],
    )


def _mouse(
    widget: ViewerWidget,
    event_type: QEvent.Type,
    x: int,
    y: int,
    *,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
) -> None:
    point = QPointF(x, y)
    event = QMouseEvent(
        event_type,
        point,
        point,
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


@pytest.mark.parametrize(
    ("mode", "shrinking", "expected"),
    [
        ("standard", True, None),
        ("moire_reduction", True, Image.Resampling.BOX),
        ("moire_reduction", False, Image.Resampling.BICUBIC),
        ("high_quality", True, Image.Resampling.LANCZOS),
        ("smooth", True, Image.Resampling.BILINEAR),
        ("pixel", True, Image.Resampling.NEAREST),
    ],
)
def test_resampling_mode_maps_to_expected_pillow_filter(
    mode: str,
    shrinking: bool,
    expected: Image.Resampling | None,
) -> None:
    target = (100, 100) if shrinking else (400, 400)
    assert pillow_resampling_for(mode, (200, 200), target) == expected


def test_render_key_carries_target_algorithm_rotation_dpr_and_crop() -> None:
    key = ViewerRenderKey(
        image_id="page",
        source_cache_key=42,
        target_width=3840,
        target_height=2160,
        mode="high_quality",
        rotation=90,
        device_pixel_ratio_milli=2000,
        crop=(10, 20, 110, 120),
        purpose="magnifier",
        request_generation=7,
    )

    assert key.target_width == 3840
    assert key.target_height == 2160
    assert key.mode == "high_quality"
    assert key.rotation == 90
    assert key.device_pixel_ratio_milli == 2000
    assert key.crop == (10, 20, 110, 120)
    assert key.request_generation == 7


def test_standard_crop_does_not_resize_but_other_modes_do() -> None:
    source = _image(100, 80)
    base = dict(
        image_id="page",
        source_cache_key=source.cacheKey(),
        target_width=300,
        target_height=200,
        rotation=0,
        device_pixel_ratio_milli=1000,
        crop=(10, 10, 60, 50),
        purpose="magnifier",
    )

    standard, standard_resized = render_qimage(
        source,
        ViewerRenderKey(mode="standard", **base),
    )
    high_quality, high_quality_resized = render_qimage(
        source,
        ViewerRenderKey(mode="high_quality", **base),
    )

    assert standard.size().toTuple() == (50, 40)
    assert not standard_resized
    assert high_quality.size().toTuple() == (300, 200)
    assert high_quality_resized


def test_async_resize_preserves_alpha() -> None:
    source = QImage(40, 40, QImage.Format.Format_RGBA8888)
    source.fill(Qt.GlobalColor.transparent)
    source.setPixelColor(20, 20, Qt.GlobalColor.red)
    key = ViewerRenderKey(
        "alpha",
        source.cacheKey(),
        80,
        80,
        "pixel",
        0,
        1000,
    )

    rendered, resized = render_qimage(source, key)

    assert resized
    assert rendered.hasAlphaChannel()
    assert rendered.pixelColor(0, 0).alpha() == 0
    assert rendered.pixelColor(40, 40).alpha() == 255


def test_config_saves_restores_modes_and_unknown_values_fall_back(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    config = ConfigManager(path)
    config.load()
    config.apply(
        {
            "viewer_resampling_mode": "pixel",
            "magnifier_resampling_mode": "smooth",
        },
        save=True,
    )

    restored = ConfigManager(path).load()
    assert restored["viewer_resampling_mode"] == "pixel"
    assert restored["magnifier_resampling_mode"] == "smooth"

    config.apply(
        {
            "viewer_resampling_mode": "future",
            "magnifier_resampling_mode": "future",
        }
    )
    assert config.get("viewer_resampling_mode") == "standard"
    assert config.get("magnifier_resampling_mode") == "standard"


def test_nonstandard_normal_render_is_async_and_keeps_current_pixmap(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    _set_single_page(widget)
    original = widget._images[0].pixmap
    widget.set_resampling_modes(normal="high_quality")
    widget.show()
    qapp.processEvents()

    widget.render(QPixmap(widget.size()))

    assert original is not None
    assert widget._last_draw_layout[0][1] is original
    assert widget._render_pending or widget._render_cache
    assert widget.wait_for_rendering()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))
    assert widget._render_cache
    key = next(iter(widget._render_cache))
    assert key.mode == "high_quality"
    assert key.target_width == widget._last_draw_layout[0][0].width()

    cached_pixmap = widget._render_cache[key]
    widget.render(QPixmap(widget.size()))
    assert widget._render_pending == {}
    assert widget._render_cache[key] is cached_pixmap
    widget.close()


def test_actual_size_avoids_unnecessary_resize(qapp: QApplication) -> None:
    widget = ViewerWidget()
    widget.resize(500, 400)
    _set_single_page(widget, image=_image(240, 160))
    widget.set_fit_mode("actual_size")
    widget.set_resampling_modes(normal="high_quality")
    widget.show()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))

    assert widget._render_pending == {}
    assert widget._render_cache == {}
    widget.close()


def test_high_dpi_physical_target_is_part_of_render_key(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    monkeypatch.setattr(widget, "devicePixelRatioF", lambda: 2.0)
    _set_single_page(widget)
    widget.set_resampling_modes(normal="pixel")
    widget.show()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))

    assert widget._render_pending
    key = next(iter(widget._render_pending))
    assert key.target_width == widget._last_draw_layout[0][0].width() * 2
    assert key.target_height == widget._last_draw_layout[0][0].height() * 2
    assert key.device_pixel_ratio_milli == 2000
    assert widget.wait_for_rendering()
    widget.close()


def test_spread_independent_layout_is_shared_by_all_modes_and_high_dpi_cache(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(1400, 900)
    monkeypatch.setattr(widget, "devicePixelRatioF", lambda: 2.0)
    large = _image(360, 650)
    small = _image(84, 120)
    widget.set_pages(
        DisplaySpread(
            0,
            (PageSlot("large", 0), PageSlot("small", 1)),
            False,
        ),
        [
            ViewerWidget.from_qimage(
                0,
                "large",
                large,
                (3600, 6500),
            ),
            ViewerWidget.from_qimage(
                1,
                "small",
                small,
                (840, 1200),
            ),
        ],
    )
    baseline_rects = widget._layout_for_current_images().rects
    for mode in RESAMPLING_MODE_LABELS:
        widget.set_resampling_modes(normal=mode)
        assert widget._layout_for_current_images().rects == baseline_rects

    widget.set_resampling_modes(normal="pixel")
    widget.show()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))
    assert widget.wait_for_rendering()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))

    keys = {
        key.image_id: key
        for key in widget._render_cache
        if key.image_id in {"large", "small"}
    }
    assert set(keys) == {"large", "small"}
    for image_id, rect in zip(("large", "small"), baseline_rects):
        assert keys[image_id].target_width == rect.width() * 2
        assert keys[image_id].target_height == rect.height() * 2

    left_key = keys["large"]
    left_pixmap = widget._render_cache[left_key]
    right_key = keys["small"]
    widget._render_cache.pop(right_key)
    layout = widget._layout_for_current_images()
    left = widget._pixmap_for_paint(widget._images[0], layout.rects[0])
    widget._pixmap_for_paint(widget._images[1], layout.rects[1])
    assert left is left_pixmap
    assert widget._layout_for_current_images().rects == baseline_rects
    assert right_key in widget._render_pending or right_key in widget._render_cache
    assert widget.wait_for_rendering()
    widget.close()


def test_stale_and_nonvisible_render_results_are_not_cached() -> None:
    widget = ViewerWidget()
    _set_single_page(widget)
    source = widget._images[0].qimage
    assert source is not None
    key = ViewerRenderKey(
        "page",
        source.cacheKey(),
        100,
        80,
        "pixel",
        0,
        1000,
    )
    task = object()
    widget._on_render_completed(
        ViewerRenderResult(key, widget._render_generation - 1, _image(), True),
        task,  # type: ignore[arg-type]
    )
    assert not widget._render_cache

    nonvisible = ViewerRenderKey(
        "old-page",
        source.cacheKey(),
        100,
        80,
        "pixel",
        0,
        1000,
    )
    widget.resampling_mode = "pixel"
    widget._on_render_completed(
        ViewerRenderResult(
            nonvisible,
            widget._render_generation,
            _image(),
            True,
        ),
        task,  # type: ignore[arg-type]
    )
    assert not widget._render_cache
    widget.close()


def test_middle_button_selects_image_and_release_atomically_activates(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    widget.set_magnifier_enabled(True)
    _set_single_page(widget)
    widget.show()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))

    _mouse(
        widget,
        QEvent.Type.MouseButtonPress,
        240,
        160,
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.MiddleButton,
    )
    assert widget.magnifier_selecting
    assert not widget.magnifier_active
    assert widget.magnifier_source_page == 0
    assert widget.magnifier_source_rect is not None
    assert widget._magnifier_selection_rect is not None
    assert widget._last_draw_layout

    _mouse(
        widget,
        QEvent.Type.MouseMove,
        300,
        170,
        button=Qt.MouseButton.NoButton,
        buttons=Qt.MouseButton.MiddleButton,
    )
    _mouse(
        widget,
        QEvent.Type.MouseButtonRelease,
        300,
        170,
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.NoButton,
    )
    assert not widget.magnifier_selecting
    assert not widget.magnifier_active
    assert widget._last_draw_layout
    assert widget.wait_for_rendering()
    qapp.processEvents()
    assert widget.magnifier_active
    assert widget._magnifier_pixmap is not None

    _mouse(
        widget,
        QEvent.Type.MouseButtonPress,
        240,
        160,
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.MiddleButton,
    )
    assert not widget.magnifier_active
    assert widget.magnifier_source_rect is None
    widget.close()


def test_middle_button_background_and_spread_gutter_do_not_start(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(600, 400)
    widget.set_magnifier_enabled(True)
    image = _image(120, 240)
    widget.set_pages(
        DisplaySpread(
            0,
            (PageSlot("left", 0), PageSlot("right", 1)),
            False,
        ),
        [
            ViewerWidget.from_qimage(0, "left", image, (120, 240)),
            ViewerWidget.from_qimage(1, "right", image, (120, 240)),
        ],
    )
    widget.show()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))
    left, right = widget._last_draw_layout
    gutter_x = (left[0].right() + right[0].left()) // 2

    for x, y in ((5, 5), (gutter_x, widget.height() // 2)):
        _mouse(
            widget,
            QEvent.Type.MouseButtonPress,
            x,
            y,
            button=Qt.MouseButton.MiddleButton,
            buttons=Qt.MouseButton.MiddleButton,
        )
        assert not widget.magnifier_selecting
    widget.close()


def test_spread_magnifier_targets_only_page_under_cursor(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(600, 400)
    widget.set_magnifier_enabled(True)
    image = _image(120, 240)
    widget.set_pages(
        DisplaySpread(
            0,
            (PageSlot("left", 0), PageSlot("right", 1)),
            False,
        ),
        [
            ViewerWidget.from_qimage(0, "left", image, (120, 240)),
            ViewerWidget.from_qimage(1, "right", image, (120, 240)),
        ],
    )
    widget.show()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))
    right_rect = widget._last_image_layout[1][0]

    _mouse(
        widget,
        QEvent.Type.MouseButtonPress,
        right_rect.center().x(),
        right_rect.center().y(),
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.MiddleButton,
    )

    assert widget.magnifier_source_page == 1
    assert widget._magnifier_source_image_id == "right"
    widget.cancel_magnifier()
    widget.close()


def test_rotated_source_mapping_is_clamped_to_rotated_bounds(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    widget.set_magnifier_enabled(True)
    widget.set_rotation_angle(90)
    _set_single_page(widget, image=_image(300, 120))
    widget.show()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))
    rect = widget._last_draw_layout[0][0]

    _mouse(
        widget,
        QEvent.Type.MouseButtonPress,
        rect.right(),
        rect.bottom(),
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.MiddleButton,
    )

    source = widget.magnifier_source_rect
    assert source is not None
    assert source.left() >= 0
    assert source.top() >= 0
    assert source.right() <= 120
    assert source.bottom() <= 300
    widget.cancel_magnifier()
    widget.close()


def test_pdf_magnifier_requests_higher_resolution_before_crop(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    widget.set_magnifier_enabled(True)
    _set_single_page(widget, image=_image(240, 160), rendered_size=(240, 160))
    requests: list[tuple[int, object]] = []
    widget.magnifierPdfResolutionRequested.connect(
        lambda page, size: requests.append((page, size))
    )
    widget.show()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))

    _mouse(
        widget,
        QEvent.Type.MouseButtonPress,
        240,
        160,
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.MiddleButton,
    )
    _mouse(
        widget,
        QEvent.Type.MouseButtonRelease,
        240,
        160,
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.NoButton,
    )

    assert requests
    assert requests[0][0] == 0
    assert requests[0][1].width() > 240
    assert widget._magnifier_waiting_for_pdf
    assert not widget.magnifier_active
    widget.close()


def test_new_page_and_escape_cancel_magnifier(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    window.viewer.magnifier_active = True
    window.viewer._magnifier_pixmap = QPixmap.fromImage(_image())
    window.viewer._magnifier_key = ViewerRenderKey(
        "page",
        1,
        100,
        100,
        "high_quality",
        0,
        1000,
        purpose="magnifier",
    )

    window._handle_escape()
    assert not window.viewer.magnifier_active
    assert not window.isFullScreen()

    window.viewer.magnifier_active = True
    window.viewer._magnifier_key = ViewerRenderKey(
        "page",
        1,
        100,
        100,
        "high_quality",
        0,
        1000,
        purpose="magnifier",
    )
    window.viewer.set_pages(
        DisplaySpread(0, (PageSlot("page", 0),), True),
        [ViewerWidget.from_qimage(0, "page", _image(), (240, 160))],
    )
    window.viewer.set_pages(
        DisplaySpread(1, (PageSlot("next", 1),), True),
        [ViewerWidget.from_qimage(1, "next", _image(), (240, 160))],
    )
    assert not window.viewer.magnifier_active
    window.close()
    qapp.processEvents()


def test_stale_magnifier_result_is_rejected() -> None:
    widget = ViewerWidget()
    first = ViewerRenderKey(
        "page",
        1,
        100,
        100,
        "high_quality",
        0,
        1000,
        crop=(0, 0, 20, 20),
        purpose="magnifier",
        request_generation=1,
    )
    current = ViewerRenderKey(
        "page",
        1,
        100,
        100,
        "high_quality",
        0,
        1000,
        crop=(0, 0, 20, 20),
        purpose="magnifier",
        request_generation=2,
    )
    widget._magnifier_key = current

    widget._on_render_completed(
        ViewerRenderResult(
            first,
            widget._render_generation,
            _image(),
            True,
        ),
        object(),  # type: ignore[arg-type]
    )

    assert not widget.magnifier_active
    assert widget._magnifier_pixmap is None
    widget.close()


def test_viewer_menu_modes_are_radio_actions_and_persist(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)

    assert {
        action.text() for action in window.normal_resampling_actions.values()
    } == set(RESAMPLING_MODE_LABELS.values())
    assert window.normal_resampling_actions["standard"].isChecked()
    assert window.magnifier_resampling_actions["high_quality"].isChecked()

    window.set_viewer_resampling_mode("pixel")
    window.set_magnifier_resampling_mode("smooth")
    assert config.get("viewer_resampling_mode") == "pixel"
    assert config.get("magnifier_resampling_mode") == "smooth"
    assert window.normal_resampling_actions["pixel"].isChecked()
    assert window.magnifier_resampling_actions["smooth"].isChecked()
    window.close()
    qapp.processEvents()
