"""Real-model/provider Browser RAM scheduling checks under Qt offscreen."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest
pytest.importorskip("PySide6")

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QListView, QLineEdit, QWidget

from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_thumbnail_memory import BrowserThumbnailMemoryBroker
from app.browser_thumbnail_memory_policy import GIB, MIB
from app.browser_workflow_controller import BrowserWorkflowController
from app.thumbnail_provider import BrowserThumbnailProvider, ThumbnailLoadResult
from app.thumbnail_render import ThumbnailRenderSpec


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    if application.platformName().casefold() != "offscreen":
        pytest.skip("Requires Qt offscreen; do not show native UI.")
    return application


@pytest.fixture
def controlled_broker(app):
    attribute = "_nivis_browser_thumbnail_memory_broker"
    previous = getattr(app, attribute, None)
    snapshot = [SimpleNamespace(
        total_physical_bytes=16 * GIB,
        available_physical_bytes=12 * GIB,
    )]
    broker = BrowserThumbnailMemoryBroker(
        app, reader=lambda: snapshot[0], clock=lambda: 0.0,
    )
    setattr(app, attribute, broker)
    try:
        yield broker, snapshot
    finally:
        broker.close()
        if previous is None:
            try:
                delattr(app, attribute)
            except AttributeError:
                pass
        else:
            setattr(app, attribute, previous)


class _Config(QObject):
    settings_changed = Signal(object)

    def __init__(self, screens=-1, mode="512"):
        super().__init__()
        self.data = {
            "browser_thumbnail_background_screens": screens,
            "browser_thumbnail_memory_mode": mode,
        }


class _Window(QWidget):
    def __init__(self, root, items, *, loader, screens=-1, mode="512"):
        super().__init__()
        self._shutdown_prepared = False
        self._generation = 0
        self._fast_scrolling = False
        self._thumbnail_scroll_direction = 1
        self._first_paint_pending_generation = None
        self._thumbnail_request_timer = None
        self._range = (0, 9)
        self._visible_row_range = lambda: self._range
        self._schedule_thumbnail_requests = lambda _delay: None
        self.thumbnail_render_spec = ThumbnailRenderSpec.from_settings(
            64, "square_1_1", "letterbox"
        )
        self.config = _Config(screens, mode)
        self.item_model = BrowserItemModel(self)
        self.item_model.set_items(items)
        self.list_view = QListView(self)
        self.list_view.setModel(self.item_model)
        self.browser_search_edit = QLineEdit(self)
        self.item_delegate = SimpleNamespace(selection_appearance=None)
        self.thumbnail_provider = BrowserThumbnailProvider(
            loader=loader, cache_capacity=512, disk_cache_enabled=False,
        )
        self._generation = self.thumbnail_provider.begin_generation()
        self.thumbnail_provider.thumbnail_ready.connect(self._on_thumbnail_ready)
        self.resize(640, 480)
        self.show()
        self.workflow = BrowserWorkflowController(self)

    def _on_thumbnail_ready(self, path, generation, image):
        if generation == self._generation:
            self.item_model.set_thumbnail_image(
                path, image,
                request_token=self.thumbnail_render_spec.cache_token,
            )

    def request_visible(self, app):
        first, last = self._range
        for row in range(first, last + 1):
            item = self.item_model.item_at(row)
            self.thumbnail_provider.request(
                item, self.thumbnail_render_spec,
                generation=self._generation,
            )
        assert self.thumbnail_provider.wait_for_done(5000)
        app.processEvents()

    def close_resources(self, app):
        self.workflow.shutdown()
        self.thumbnail_provider.close()
        self.close()
        self.deleteLater()
        app.processEvents()


def _mixed_items(root, count):
    kinds = (
        (BrowserItemKind.IMAGE, ".jpg"),
        (BrowserItemKind.FOLDER, ""),
        (BrowserItemKind.ARCHIVE, ".zip"),
        (BrowserItemKind.PDF, ".pdf"),
    )
    items = []
    for row in range(count):
        kind, extension = kinds[row % len(kinds)]
        name = f"{row:04d}-{kind.value}{extension}"
        items.append(BrowserItem(
            name, root / name, kind, float(row),
            file_size=row + 1, modified_time_ns=row + 1,
        ))
    return items


def _drain_workflow(app, window, timeout=5.0):
    import time
    window.workflow._pump()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        window.thumbnail_provider.wait_for_done(25)
        app.processEvents()
        if (window.thumbnail_provider.pending_count == 0
                and window.workflow.stop_reason in {
                    "range-complete", "persistence-unavailable",
                    "far-cache-capacity",
                }
                and not window.workflow._timer.isActive()):
            return
        time.sleep(0.001)
    pytest.fail(
        f"Browser warmup did not settle: {window.workflow.stop_reason}; "
        f"{window.thumbnail_provider.cache_statistics()}"
    )


def test_scrolls_forward_and_back_over_mixed_kinds_with_finite_ram_window(
    tmp_path, app, controlled_broker,
):
    _broker, _snapshot = controlled_broker
    loaded_kinds = set()

    def loader(item, _size, _cancel_token):
        loaded_kinds.add(item.kind)
        image = QImage(24, 32, QImage.Format.Format_RGBA8888)
        image.fill(0xFF2367A1)
        return image

    window = _Window(tmp_path, _mixed_items(tmp_path, 240), loader=loader)
    try:
        positions = [(first, first + 9) for first in range(0, 100, 10)]
        for index, region in enumerate([*positions, *reversed(positions[:-1])]):
            window._range = region
            window._thumbnail_scroll_direction = (
                -1 if index >= len(positions) else 1
            )
            window.workflow.recenter_cache_retention()
            window.request_visible(app)
            _drain_workflow(app, window)

            first, last = region
            next_rows = (
                range(first - 10, first) if window._thumbnail_scroll_direction < 0
                else range(last + 1, last + 11)
            )
            for row in next_rows:
                if not 0 <= row < window.item_model.rowCount():
                    continue
                item = window.item_model.item_at(row)
                assert window.thumbnail_provider.has_memory_thumbnail(
                    item, window.thumbnail_render_spec
                ), f"next viewport row {row} was not ready from RAM"
            assert len(window.item_model._thumbnail_images) <= 10
            diagnostics = window.thumbnail_provider.browser_memory_diagnostics()
            assert diagnostics["bytes"] <= diagnostics["limit_bytes"]

        assert loaded_kinds == {
            BrowserItemKind.IMAGE, BrowserItemKind.FOLDER,
            BrowserItemKind.ARCHIVE, BrowserItemKind.PDF,
        }
        assert window.thumbnail_provider.cache_statistics()[
            "generated_background"
        ] > 100
    finally:
        window.close_resources(app)


def test_unlimited_work_stops_after_hot_window_when_disk_route_is_unavailable(
    tmp_path, app, controlled_broker,
):
    _broker, _snapshot = controlled_broker

    def loader(_item, _size, _cancel_token):
        image = QImage(16, 16, QImage.Format.Format_RGB32)
        image.fill(0xFF884422)
        return image

    window = _Window(tmp_path, _mixed_items(tmp_path, 300), loader=loader)
    try:
        window._range = (100, 109)
        window.workflow.recenter_cache_retention()
        window.request_visible(app)
        _drain_workflow(app, window)
        stats = window.thumbnail_provider.cache_statistics()
        # Current 10 plus three screens on each side bounds RAM refill to 70
        # rows; already-present visible images are not counted as background.
        assert stats["generated_background"] <= 60
        assert window.workflow.stop_reason == "persistence-unavailable"
        assert window.thumbnail_provider.memory_cache_bytes <= (
            window.thumbnail_provider.memory_cache_limit_bytes
        )
    finally:
        window.close_resources(app)


def test_large_selection_retention_work_is_bounded_by_visible_scope(
    tmp_path, app, controlled_broker,
):
    _broker, _snapshot = controlled_broker

    def loader(_item, _size, _cancel_token):
        return None

    window = _Window(tmp_path, _mixed_items(tmp_path, 16000), loader=loader)
    try:
        model = window.item_model
        selection = QItemSelection(model.index(1000, 0), model.index(15999, 0))
        selection_model = window.list_view.selectionModel()
        selection_model.select(
            selection, QItemSelectionModel.SelectionFlag.Select,
        )
        class RangeOnlySelection:
            def selection(self):
                return selection

            def selectedIndexes(self):
                pytest.fail("large selection must not materialize every QModelIndex")

        window.list_view.selectionModel = lambda: RangeOnlySelection()
        calls = 0
        item_at = model.item_at

        def counted_item_at(row):
            nonlocal calls
            calls += 1
            return item_at(row)

        model.item_at = counted_item_at
        window.workflow._update_cache_retention(0, 19, 1, 3)

        # Visible/near/hot rows are visited a bounded number of times; the
        # 15,000 selected rows are read as a selection range and capped at 128.
        assert calls < 700
        selected_priorities = [
            rank for (path, _revision), rank
            in window.thumbnail_provider._cache_retention_rank.items()
            if path.casefold().endswith(".pdf")
        ]
        assert len(selected_priorities) <= 128 + 40
    finally:
        window.close_resources(app)


def test_multiple_browsers_shrink_on_pressure_and_release_clients(
    tmp_path, app, controlled_broker,
):
    broker, snapshots = controlled_broker

    def loader(_item, _size, _cancel_token):
        image = QImage(48, 48, QImage.Format.Format_RGBA8888)
        image.fill(0xFF447799)
        return image

    windows = [
        _Window(tmp_path / f"window-{index}", _mixed_items(tmp_path, 20),
                loader=loader, mode="512")
        for index in range(2)
    ]
    try:
        for window in windows:
            window.workflow.recenter_cache_retention()
        broker.sample_now()
        grants = [window.thumbnail_provider.memory_cache_limit_bytes for window in windows]
        assert sum(grants) <= broker._governor.capacity
        assert all(grant > 0 for grant in grants)

        for window in windows:
            image = QImage(48, 48, QImage.Format.Format_RGBA8888)
            image.fill(0xFFAA5544)
            item = window.item_model.item_at(0)
            window.thumbnail_provider._consume_thumbnail_finished(
                str(item.path), window._generation,
                window.thumbnail_render_spec.cache_token,
                item.thumbnail_revision, ThumbnailLoadResult(image),
            )
        retained_browser = sum(
            window.thumbnail_provider.memory_cache_bytes for window in windows
        )
        assert retained_browser > 0

        # This lower OS availability stands in for pressure from the whole
        # process, including Viewer; only Browser provider bytes are added back.
        snapshots[0] = SimpleNamespace(
            total_physical_bytes=16 * GIB, available_physical_bytes=0,
        )
        broker.sample_now()
        assert all(
            window.thumbnail_provider.memory_cache_limit_bytes < grant
            for window, grant in zip(windows, grants)
        )
        assert all(window.thumbnail_provider.memory_cache_bytes == 0 for window in windows)

        windows[0].workflow.shutdown()
        assert id(windows[0].workflow) not in broker._clients
        assert broker._timer.isActive()
        windows[1].workflow.shutdown()
        assert not broker._timer.isActive()
    finally:
        for window in windows:
            window.close_resources(app)
