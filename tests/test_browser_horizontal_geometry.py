"""Synthetic-only measurements of real QListView cells and delegate pixels."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QListView

from app.browser_item_delegate import BrowserItemDelegate, thumbnail_content_rect, type_badge_rect
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_sort import BrowserDisplayDensity


BLUE = QColor('#1879d3')
OLD_MARGINS = dict(zip(BrowserDisplayDensity, (4, 20, 20, 44, 72, 104)))


class LegacyMarginDelegate(BrowserItemDelegate):
    """Before-state fixture only; all layout and painting remain production code."""
    @property
    def profile(self):
        return replace(super().profile, horizontal_margin=OLD_MARGINS[self.density])


class NoShellIcons:
    def image_for(self, *args, **kwargs):
        return QImage()


def synthetic_grid(qapp, tmp_path, density, width, *, image_size=QSize(388, 564),
                   delegate_type=BrowserItemDelegate, **options):
    view = QListView()
    delegate = delegate_type(view, thumbnail_size=149, density=density,
                                   shell_icon_provider=NoShellIcons(), **options)
    model = BrowserItemModel(view)
    items = [BrowserItem(f'画像 {i:03d} long filename.jpg', tmp_path / f'{i:03d}.jpg',
                         BrowserItemKind.IMAGE, None) for i in range(100)]
    model.set_items(items)
    image = QImage(image_size, QImage.Format.Format_RGB32)
    image.fill(BLUE)
    for item in items:
        model.set_thumbnail_image(item.path, image)
    view.setModel(model)
    view.setItemDelegate(delegate)
    view.setViewMode(QListView.ViewMode.IconMode)
    view.setResizeMode(QListView.ResizeMode.Adjust)
    view.setMovement(QListView.Movement.Static)
    view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
    view.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
    view.setUniformItemSizes(True)
    view.setIconSize(QSize(149, 149))
    view.setGridSize(delegate.grid_metrics.grid_size)
    view.setSpacing(0)
    view.setWordWrap(delegate.grid_metrics.title_lines > 1)
    palette = QPalette(view.palette())
    palette.setColor(QPalette.ColorRole.Base, QColor('white'))
    view.setPalette(palette)
    view.resize(width, 420)
    view.show()
    view.resize(width, 420)
    view.doItemsLayout()
    qapp.processEvents()
    view.setCurrentIndex(model.index(-1, 0))
    view.clearSelection()
    return view, delegate, model


def measure(view, delegate, model):
    cells = [view.visualRect(model.index(i, 0)) for i in range(20)]
    columns = next(i for i, cell in enumerate(cells) if cell.top() != cells[0].top())
    frames = [delegate.grid_metrics.thumbnail_frame_rect(cell) for cell in cells]
    pixmap = view.viewport().grab()
    image = pixmap.toImage()
    dpr = pixmap.devicePixelRatio()
    y = round(frames[0].center().y() * dpr)
    def colored_runs(values):
        runs = []
        for value in values:
            if not runs or value > runs[-1][1] + 1:
                runs.append([value, value])
            else:
                runs[-1][1] = value
        return runs
    runs = colored_runs(x for x in range(image.width()) if image.pixelColor(x, y) == BLUE)
    # Sample near the colored right edge, away from top-left stars and the
    # lower-left associated icon; those overlays are not inter-row spacing.
    x = runs[0][1] - 1
    vertical_runs = colored_runs(y for y in range(image.height()) if image.pixelColor(x, y) == BLUE)
    return dict(viewport=view.viewport().width(), columns=columns,
                cell_width=cells[0].width(), frame_width=frames[0].width(),
                item_gap=cells[1].left()-cells[0].right()-1,
                frame_gap=frames[1].left()-frames[0].right()-1,
                pixel_gap=(runs[1][0]-runs[0][1]-1)/dpr,
                pixel_width=(runs[0][1]-runs[0][0]+1)/dpr,
                vertical_pixel_gap=(vertical_runs[1][0]-vertical_runs[0][1]-1)/dpr,
                vertical_frame_gap=frames[columns].top()-frames[0].bottom()-1,
                row_pitch=cells[columns].top()-cells[0].top(),
                right_remainder=view.viewport().width()-columns*view.gridSize().width())


@pytest.mark.parametrize('density', list(BrowserDisplayDensity))
@pytest.mark.parametrize('width', [452, 760, 761, 927])
def test_measured_horizontal_gap_reduction_preserves_pixels_and_rows(qapp, tmp_path, density, width):
    old_view, old_delegate, old_model = synthetic_grid(qapp, tmp_path, density, width,
                                                     delegate_type=LegacyMarginDelegate)
    view, delegate, model = synthetic_grid(qapp, tmp_path, density, width)
    try:
        before = measure(old_view, old_delegate, old_model)
        result = measure(view, delegate, model)
        print(density.value, width, 'before', before, 'after', result)
        assert result['frame_width'] == 105
        assert result['item_gap'] == 0
        assert result['frame_gap'] == 4
        assert before['frame_gap'] == OLD_MARGINS[density]
        assert result['cell_width'] == 109
        assert abs(result['pixel_gap'] - 12) <= 1 / view.devicePixelRatioF()
        assert abs(result['pixel_width'] - before['pixel_width']) <= 1 / view.devicePixelRatioF()
        assert result['vertical_frame_gap'] == before['vertical_frame_gap']
        assert result['row_pitch'] == before['row_pitch']
        # Fractional-DPI x-origin changes can round the available Fit width by
        # one physical pixel and hence move each aspect-fit vertical edge by
        # one pixel. Logical frame/row geometry above must remain exact.
        assert abs(result['vertical_pixel_gap'] - before['vertical_pixel_gap']) <= 2 / view.devicePixelRatioF() + 1e-6
        # QListView's existing right-edge boundary is strict at exact multiples.
        assert result['columns'] == max(1, (result['viewport'] - 1) // 109)
        assert 0 < result['right_remainder'] <= 109
        assert view.horizontalScrollBar().maximum() == 0
        last = view.visualRect(model.index(result['columns']-1, 0))
        assert last.right() < view.viewport().width()
    finally:
        old_view.close()
        view.close()


@pytest.mark.parametrize('display', ['hidden', 'one_line', 'two_lines'])
@pytest.mark.parametrize('padding', [0, 3])
def test_explicit_padding_labels_and_hits_remain_authoritative(qapp, tmp_path, display, padding):
    view, delegate, model = synthetic_grid(qapp, tmp_path, BrowserDisplayDensity.MEDIUM, 760,
                                           filename_display=display, cell_padding=padding,
                                           item_spacing_x=7, item_spacing_y=9,
                                           filename_gap=2, filename_padding_y=1)
    try:
        result = measure(view, delegate, model)
        assert result['frame_gap'] == 4 + 2 * padding + 7
        assert result['item_gap'] == 7
        assert result['row_pitch'] == delegate.cell_size.height() + 9
        assert view.spacing() == 0
        cell = view.visualRect(model.index(1, 0))
        frame = delegate.grid_metrics.thumbnail_frame_rect(cell)
        assert frame.size() == QSize(105, 149)
        assert frame.contains(delegate.rating_overlay_rect(cell))
        assert frame.contains(type_badge_rect(frame))
        assert delegate.rating_at_position(cell, delegate.rating_overlay_rect(cell).center()) in range(1, 6)
        assert delegate.grid_metrics.selection_rect(cell).width() == 102
        title = delegate.grid_metrics.title_rect(cell)
        assert title.isEmpty() == (display == 'hidden')
        if display != 'hidden':
            assert cell.contains(title)
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=title.center())
            assert view.currentIndex().row() == 1
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=frame.center())
        assert view.currentIndex().row() == 1
        assert model.item_at(view.currentIndex()).display_name == '画像 001 long filename.jpg'
        QTest.keyClick(view, Qt.Key.Key_Right)
        assert view.currentIndex().row() == 2
        QTest.keyClick(view, Qt.Key.Key_Down)
        assert view.currentIndex().row() == 2 + result['columns']
    finally:
        view.close()


def test_fit_letterboxing_is_not_mistaken_for_cell_spacing(qapp, tmp_path):
    view, delegate, model = synthetic_grid(qapp, tmp_path, BrowserDisplayDensity.MEDIUM, 760,
                                           image_size=QSize(300, 900))
    try:
        result = measure(view, delegate, model)
        assert result['frame_gap'] == 4
        assert result['pixel_gap'] > 50  # Narrow synthetic source retains its aspect, not stretched.
        frame = delegate.grid_metrics.thumbnail_frame_rect(view.visualRect(model.index(0, 0)))
        assert result['pixel_width'] < thumbnail_content_rect(frame).width()
    finally:
        view.close()
